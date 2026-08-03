"""验证 Ollama 模型适配器对 HTTP 与连接错误的用户友好转换。"""

import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

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


if __name__ == "__main__":
    # 支持直接运行该文件，以便在本地快速检查网络错误转换逻辑。
    unittest.main()
