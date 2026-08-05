"""验证 Ollama 模型适配器对 HTTP 与连接错误的用户友好转换。"""

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from evalhub.adapters.base import ModelGenerationError
from evalhub.adapters.ollama import OllamaAdapter


class OllamaAdapterTest(unittest.TestCase):
    """隔离真实网络后保护 Ollama 请求失败时的诊断信息。"""

    def test_http_error_includes_ollama_response_body(self) -> None:
        """HTTP 错误应优先暴露 Ollama JSON 正文中的具体失败原因。"""
        # 构造带结构化错误正文的 500 响应，模拟模型进程异常终止场景。
        error = HTTPError(
            url="http://127.0.0.1:11434/api/generate",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(b'{"error":"llama-server process has terminated: signal: abort trap"}'),
        )

        # 在标准库网络边界注入错误，确保测试不依赖本机 Ollama 安装和服务状态。
        with patch("evalhub.adapters.ollama.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "abort trap"):
                OllamaAdapter(model="qwen2.5:0.5b").generate("hello")

    def test_url_error_reports_connection_problem(self) -> None:
        """连接失败应转换为包含启动指引的项目级运行错误。"""
        # 模拟端口拒绝连接，验证适配器不会把底层 ``URLError`` 直接泄漏给用户。
        with patch("evalhub.adapters.ollama.urlopen", side_effect=URLError("connection refused")):
            with self.assertRaisesRegex(RuntimeError, "无法连接 Ollama 服务"):
                OllamaAdapter(model="qwen2.5:0.5b").generate("hello")

    def test_generate_sends_think_at_top_level_and_returns_finish_diagnostics(self) -> None:
        """思考开关应位于请求顶层，响应应保留可复现的完成原因和 token 数。"""
        response = _Response(
            b'{"response":"B","done":true,"done_reason":"length","eval_count":256}'
        )
        with patch("evalhub.adapters.ollama.urlopen", return_value=response) as mocked:
            result = OllamaAdapter(model="gemma4:12b").generate(
                "question", think=False, temperature=0, num_predict=256
            )

        # 读取实际请求 JSON，验证顶层协议字段没有被错误塞入 options。
        request = mocked.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["think"], False)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 256})
        self.assertEqual(result.text, "B")
        self.assertEqual(result.done_reason, "length")
        self.assertEqual(result.output_tokens, 256)

    def test_empty_length_response_is_generation_incomplete(self) -> None:
        """思考预算耗尽且最终回答为空时应阻塞节点而不是记录成零分。"""
        response = _Response(b'{"response":"","done":true,"done_reason":"length"}')

        with patch("evalhub.adapters.ollama.urlopen", return_value=response):
            with self.assertRaises(ModelGenerationError) as raised:
                OllamaAdapter(model="gemma4:12b").generate("question")

        self.assertEqual(raised.exception.code, "generation_incomplete")

    def test_other_empty_response_has_stable_error_code(self) -> None:
        """正常停止却没有文本时应标记为空模型响应而不是构造空预测。"""
        response = _Response(b'{"response":"","done":true,"done_reason":"stop"}')

        with patch("evalhub.adapters.ollama.urlopen", return_value=response):
            with self.assertRaises(ModelGenerationError) as raised:
                OllamaAdapter(model="qwen2.5:0.5b").generate("question")

        self.assertEqual(raised.exception.code, "empty_model_response")

    def test_generate_rejects_invalid_response_field_types(self) -> None:
        """Ollama 成功响应的三个关键字段必须保持正式 API 类型。"""
        invalid_bodies = (
            b'{"response":null,"done":true,"done_reason":"stop"}',
            b'{"response":"A","done":"true","done_reason":"stop"}',
            b'{"response":"A","done":true,"done_reason":null}',
        )

        for body in invalid_bodies:
            with self.subTest(body=body), patch(
                "evalhub.adapters.ollama.urlopen", return_value=_Response(body)
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected Ollama response"):
                    OllamaAdapter(model="qwen2.5:0.5b").generate("question")


class _Response:
    """模拟 Ollama 生成接口返回的上下文响应。"""

    def __init__(self, body: bytes) -> None:
        """保存一次测试调用需要读取的固定 JSON 字节。"""
        self.body = body

    def __enter__(self) -> "_Response":
        """返回当前响应对象以匹配 urllib 上下文协议。"""
        return self

    def __exit__(self, *args: object) -> None:
        """结束模拟响应且不抑制测试中的异常。"""
        return None

    def read(self) -> bytes:
        """返回构造时固定的响应正文。"""
        return self.body


if __name__ == "__main__":
    # 支持直接运行该文件，以便在本地快速检查网络错误转换逻辑。
    unittest.main()
