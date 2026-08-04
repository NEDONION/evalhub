"""通过 OpenAI-compatible Chat Completions 协议调用远程模型。"""

from __future__ import annotations

import json
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evalhub.adapters.base import ModelAdapter

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_FINAL_ONLY_SYSTEM_PROMPT = (
    "Follow the requested answer format exactly. Do not show reasoning or explanations."
)


class OpenAICompatibleAdapter(ModelAdapter):
    """把统一文本生成接口映射到非流式 Chat Completions 请求。"""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        *,
        provider_id: str | None = None,
    ) -> None:
        """固定单次评测使用的模型、服务地址和短生命周期凭据。

        Args:
            model: 服务商公开的模型 ID。
            base_url: 已由服务商仓储校验的 API 根地址。
            api_key: 仅保存在当前适配器实例中的完整访问凭据。
            provider_id: 内置服务商标识；用于启用厂商明确支持的协议参数。

        Raises:
            ValueError: 模型、地址或凭据为空。
        """
        if not model.strip() or not base_url.strip() or not api_key:
            raise ValueError("model, Base URL, and API Key are required")
        # 默认对象 repr 不展开实例属性，避免调试输出意外包含凭据。
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.provider_id = provider_id

    def generate(self, prompt: str, **kwargs: object) -> str:
        """发送单轮用户消息并返回首个助手响应文本。

        Args:
            prompt: 当前评测样本的完整文本输入。
            **kwargs: 支持温度、采样概率、生成上限和随机种子；其他字段忽略。

        Returns:
            ``choices[0].message.content`` 中的文本。

        Raises:
            RuntimeError: 网络失败、上游拒绝或响应不符合兼容协议。
        """
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        # DeepSeek V4 默认思考会占用 Benchmark 的短输出预算；评测只需要协议要求的最终答案。
        if self.provider_id == "deepseek":
            payload["messages"] = [
                {"role": "system", "content": _FINAL_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            payload["thinking"] = {"type": "disabled"}
        # 评测配置只透传协议明确支持的采样字段，并转换 Ollama 的长度字段名。
        for key in ("temperature", "top_p", "seed"):
            if key in kwargs and kwargs[key] is not None:
                payload[key] = kwargs[key]
        if "num_predict" in kwargs and kwargs["num_predict"] is not None:
            payload["max_tokens"] = kwargs["num_predict"]

        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=_headers(self._api_key),
            method="POST",
        )
        body = _request_json(
            request,
            api_key=self._api_key,
            timeout=300,
            max_attempts=_MAX_ATTEMPTS,
        )
        return _completion_text(body)


def discover_models(base_url: str, api_key: str) -> list[str]:
    """读取服务商 ``/models`` 端点并返回可选择的模型 ID。

    Args:
        base_url: 已校验的 API 根地址。
        api_key: 当前服务商的解密访问凭据。

    Returns:
        过滤非法项后去重并按字典序排列的模型 ID。

    Raises:
        RuntimeError: 网络、认证或响应结构无效。
        ValueError: 地址或凭据为空。
    """
    if not base_url.strip() or not api_key:
        raise ValueError("Base URL and API Key are required")
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers=_headers(api_key),
        method="GET",
    )
    body = _request_json(request, api_key=api_key, timeout=10, max_attempts=1)

    # OpenAI-compatible 列表使用顶层 data 数组，每项必须提供非空字符串 ID。
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise RuntimeError("模型服务响应不符合 OpenAI-compatible Models 协议")
    model_ids = {
        item["id"].strip()
        for item in body["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
    }
    return sorted(model_ids)


def _headers(api_key: str) -> dict[str, str]:
    """构造兼容接口需要的 JSON 与 Bearer 请求头。

    Args:
        api_key: 当前请求使用的完整 API Key。

    Returns:
        仅交给 ``urllib`` 请求对象的认证和内容类型请求头。
    """
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request_json(
    request: Request,
    *,
    api_key: str,
    timeout: int,
    max_attempts: int,
) -> object:
    """执行 JSON 请求并对有限的临时 HTTP 错误进行重试。

    Args:
        request: 不会被日志化的完整 ``urllib`` 请求对象。
        api_key: 仅用于从异常详情中精确脱敏的当前凭据。
        timeout: 单次请求超时秒数。
        max_attempts: 包含首次请求的最大尝试次数。

    Returns:
        JSON 解码后的任意顶层对象，由具体协议解析器继续收窄。

    Raises:
        RuntimeError: HTTP、连接、超时或 JSON 解码失败。
    """
    for attempt in range(max_attempts):
        try:
            # 完整生成允许较长超时，模型发现由调用方传入更短上限。
            with urlopen(request, timeout=timeout) as response:
                raw_body = response.read()
            return json.loads(raw_body.decode("utf-8"))
        except HTTPError as exc:
            retryable = exc.code in _RETRYABLE_STATUS and attempt + 1 < max_attempts
            if retryable:
                sleep(_retry_delay(exc, attempt))
                continue
            raise RuntimeError(_http_error_message(exc, api_key)) from exc
        except (URLError, TimeoutError) as exc:
            # 网络异常原因可能包含完整 URL 或服务商回显，统一执行精确凭据替换。
            detail = _redact(str(getattr(exc, "reason", exc)), api_key)
            raise RuntimeError(f"无法连接模型服务：{detail}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("模型服务返回了无效的 JSON 响应") from exc
    raise RuntimeError("模型服务请求达到未预期的重试状态")


def _retry_delay(error: HTTPError, attempt: int) -> float:
    """优先解析 Retry-After，否则返回有上限的短指数退避时间。

    Args:
        error: 当前可重试 HTTP 错误。
        attempt: 从零开始的已失败尝试序号。

    Returns:
        不超过 30 秒的等待秒数。
    """
    retry_after = error.headers.get("Retry-After") if error.headers is not None else None
    if retry_after is not None:
        try:
            # 兼容厂商常见的整数和小数字符串，日期格式交给指数退避路径。
            return min(max(float(retry_after), 0.0), 30.0)
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 4.0)


def _http_error_message(error: HTTPError, api_key: str) -> str:
    """把 HTTP 状态和有限上游详情转换为脱敏用户错误。

    Args:
        error: 已停止重试的上游 HTTP 错误。
        api_key: 需要从任何上游回显中移除的完整凭据。

    Returns:
        不包含认证头、密钥或超长响应正文的错误文本。
    """
    if error.code == 401:
        return "模型服务认证失败：API Key 无效或已失效"
    if error.code == 403:
        return "模型服务拒绝访问：账号无权使用目标模型"

    # 其他状态保留短错误详情帮助定位模型名、配额或服务可用性问题。
    raw_body = error.fp.read(4096) if error.fp is not None else b""
    detail = _error_detail(raw_body, api_key)
    suffix = f"。{detail}" if detail else ""
    return f"模型服务请求失败：HTTP {error.code}{suffix}"


def _error_detail(raw_body: bytes, api_key: str) -> str:
    """从上游错误正文提取有限文本并精确移除当前 API Key。

    Args:
        raw_body: 最多读取 4096 字节的上游错误正文。
        api_key: 可能被上游错误错误回显的完整凭据。

    Returns:
        最多 500 字符的脱敏错误描述。
    """
    text = raw_body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        # 常见兼容服务把具体原因放在 error.message，未知结构只返回紧凑 JSON。
        error_value = parsed.get("error") if isinstance(parsed, dict) else parsed
        if isinstance(error_value, dict) and isinstance(error_value.get("message"), str):
            detail = error_value["message"]
        else:
            detail = json.dumps(error_value, ensure_ascii=False, separators=(",", ":"))
    return _redact(detail.strip(), api_key)[:500]


def _redact(value: str, api_key: str) -> str:
    """从诊断文本中精确替换当前请求使用的完整凭据。

    Args:
        value: 可能来自网络异常或上游正文的文本。
        api_key: 禁止出现在异常消息中的完整凭据。

    Returns:
        已用固定占位符替换凭据的文本。
    """
    return value.replace(api_key, "[REDACTED]") if api_key else value


def _completion_text(body: object) -> str:
    """从 Chat Completions 响应中读取首个助手文本。

    Args:
        body: JSON 解码后的上游响应对象。

    Returns:
        第一个 choice 中的字符串 ``message.content``。

    Raises:
        RuntimeError: choices、message 或 content 缺失或类型错误。
    """
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
        raise RuntimeError("模型服务响应不符合 Chat Completions 协议")
    choices = body["choices"]
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError("模型服务响应不符合 Chat Completions 协议")

    # 协议只接受标准 message.content 文本，不把工具调用或其他结构强制转成字符串。
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise RuntimeError("模型服务响应不符合 Chat Completions 协议")
    return message["content"]
