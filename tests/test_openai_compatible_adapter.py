"""验证 OpenAI-compatible 请求映射、重试、脱敏和模型发现。"""

import json
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Self
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from evalhub.adapters.openai_compatible import OpenAICompatibleAdapter, discover_models
from evalhub.cli import build_model_adapter
from evalhub.credentials import CredentialCipher
from evalhub.model_providers import ModelProviderRepository


class _Response:
    """提供 ``urlopen`` 上下文协议和固定字节正文的轻量测试响应。"""

    def __init__(self, body: bytes) -> None:
        """保存一次请求需要读取的 UTF-8 JSON 字节。

        Args:
            body: 模拟上游服务返回的完整响应正文。
        """
        self.body = body

    def __enter__(self) -> Self:
        """进入网络响应上下文并返回当前测试对象。"""
        return self

    def __exit__(self, *args: object) -> None:
        """退出响应上下文；内存响应无需释放外部资源。"""

    def read(self) -> bytes:
        """返回构造时保存的固定响应正文。"""
        return self.body


def _http_error(status: int, body: str, *, retry_after: str | None = None) -> HTTPError:
    """构造带可读取 JSON 正文和可选重试头的 HTTP 错误。

    Args:
        status: 模拟的 HTTP 状态码。
        body: 上游错误正文。
        retry_after: 可选的数字型等待秒数。

    Returns:
        可直接作为 mock side effect 抛出的 ``HTTPError``。
    """
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        url="https://api.example.com/chat/completions",
        code=status,
        msg="upstream error",
        hdrs=headers,
        fp=BytesIO(body.encode("utf-8")),
    )


def test_generate_maps_evalhub_options_to_chat_completions() -> None:
    """生成调用应只透传受支持参数并解析首个助手文本。"""
    response = _Response(b'{"choices":[{"message":{"content":"42"}}]}')
    with patch("evalhub.adapters.openai_compatible.urlopen", return_value=response) as opener:
        result = OpenAICompatibleAdapter(
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/",
            api_key="sk-secret",
        ).generate(
            "6 * 7",
            temperature=0,
            top_p=0.9,
            num_predict=256,
            seed=7,
            ignored=True,
        )

    # 请求路径、认证头与 JSON 载荷共同验证现有评测参数到协议字段的完整映射。
    request = opener.call_args.args[0]
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-secret"
    assert json.loads(request.data) == {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "6 * 7"}],
        "stream": False,
        "temperature": 0,
        "top_p": 0.9,
        "max_tokens": 256,
        "seed": 7,
    }
    assert opener.call_args.kwargs["timeout"] == 300
    assert result == "42"


def test_generate_retries_rate_limit_then_returns_success() -> None:
    """429 应遵循数字型 Retry-After 并在第二次请求成功后停止重试。"""
    limited = _http_error(429, '{"error":{"message":"busy"}}', retry_after="0")
    success = _Response(b'{"choices":[{"message":{"content":"ok"}}]}')

    with (
        patch(
            "evalhub.adapters.openai_compatible.urlopen", side_effect=[limited, success]
        ) as opener,
        patch("evalhub.adapters.openai_compatible.sleep") as sleeper,
    ):
        result = OpenAICompatibleAdapter("model", "https://api.example.com", "sk-key").generate(
            "hello"
        )

    assert result == "ok"
    assert opener.call_count == 2
    sleeper.assert_called_once_with(0.0)


def test_generate_does_not_retry_unauthorized_or_leak_key() -> None:
    """401 必须立即失败，且异常消息不能包含请求凭据或上游回显的完整密钥。"""
    api_key = "sk-never-show-this"
    unauthorized = _http_error(401, f'{{"error":{{"message":"bad {api_key}"}}}}')

    with patch(
        "evalhub.adapters.openai_compatible.urlopen", side_effect=unauthorized
    ) as opener:
        with pytest.raises(RuntimeError, match="API Key") as captured:
            OpenAICompatibleAdapter("model", "https://api.example.com", api_key).generate("hello")

    assert opener.call_count == 1
    assert api_key not in str(captured.value)


def test_generate_stops_after_three_retryable_failures() -> None:
    """持续 503 最多请求三次，并返回不含凭据的脱敏错误。"""
    api_key = "sk-retry-secret"
    errors = [_http_error(503, f"unavailable {api_key}") for _ in range(3)]

    with (
        patch("evalhub.adapters.openai_compatible.urlopen", side_effect=errors) as opener,
        patch("evalhub.adapters.openai_compatible.sleep"),
    ):
        with pytest.raises(RuntimeError, match="HTTP 503") as captured:
            OpenAICompatibleAdapter("model", "https://api.example.com", api_key).generate("hello")

    assert opener.call_count == 3
    assert api_key not in str(captured.value)


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{}}]}',
        b'{"choices":[{"message":{"content":null}}]}',
    ],
)
def test_generate_rejects_malformed_chat_completion(body: bytes) -> None:
    """缺少首个助手文本的上游响应不能被当作有效预测。"""
    with patch("evalhub.adapters.openai_compatible.urlopen", return_value=_Response(body)):
        with pytest.raises(RuntimeError, match="Chat Completions"):
            OpenAICompatibleAdapter("model", "https://api.example.com", "sk-key").generate(
                "hello"
            )


def test_discover_models_returns_sorted_unique_ids() -> None:
    """模型探测应使用 GET 请求并过滤、去重和排序合法模型 ID。"""
    response = _Response(
        b'{"data":[{"id":"z-model"},{"id":"a-model"},{"id":"z-model"},{"id":3}]}'
    )
    with patch("evalhub.adapters.openai_compatible.urlopen", return_value=response) as opener:
        models = discover_models("https://api.example.com/v1/", "sk-secret")

    request = opener.call_args.args[0]
    assert request.full_url == "https://api.example.com/v1/models"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer sk-secret"
    assert opener.call_args.kwargs["timeout"] == 10
    assert models == ["a-model", "z-model"]


def test_build_model_adapter_resolves_key_without_persisting_it(tmp_path: Path) -> None:
    """统一构造入口应按服务商 ID 解密凭据，适配器展示不得泄漏明文。"""
    repository = ModelProviderRepository(
        tmp_path / "providers.sqlite3",
        CredentialCipher.from_runtime(tmp_path, env={}),
    )
    repository.save(
        "deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key="sk-secret",
    )

    adapter = build_model_adapter(
        "openai-compatible",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        oracle_responses={},
        provider_id="deepseek",
        provider_repository=repository,
    )

    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert "sk-secret" not in repr(adapter)
