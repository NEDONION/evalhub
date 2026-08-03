from io import BytesIO
from urllib.error import HTTPError, URLError
import unittest
from unittest.mock import patch

from evalhub.adapters.ollama import OllamaAdapter


class OllamaAdapterTest(unittest.TestCase):
    def test_http_error_includes_ollama_response_body(self) -> None:
        error = HTTPError(
            url="http://127.0.0.1:11434/api/generate",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(b'{"error":"llama-server process has terminated: signal: abort trap"}'),
        )

        with patch("evalhub.adapters.ollama.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "abort trap"):
                OllamaAdapter(model="qwen2.5:0.5b").generate("hello")

    def test_url_error_reports_connection_problem(self) -> None:
        with patch("evalhub.adapters.ollama.urlopen", side_effect=URLError("connection refused")):
            with self.assertRaisesRegex(RuntimeError, "无法连接 Ollama 服务"):
                OllamaAdapter(model="qwen2.5:0.5b").generate("hello")


if __name__ == "__main__":
    unittest.main()
