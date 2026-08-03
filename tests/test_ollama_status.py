import unittest
from unittest.mock import patch
from urllib.error import URLError


class OllamaStatusTest(unittest.TestCase):
    def test_status_reports_not_installed_when_command_missing(self) -> None:
        from evalhub.ollama import get_ollama_status

        with patch("evalhub.ollama.find_ollama_command", return_value=None):
            status = get_ollama_status(model="qwen2.5:0.5b")

        self.assertEqual(status["installed"], False)
        self.assertEqual(status["running"], False)
        self.assertEqual(status["model_present"], False)

    def test_status_reports_running_and_model_present(self) -> None:
        from evalhub.ollama import get_ollama_status

        response = _Response(
            b'{"models":[{"name":"qwen2.5:0.5b"},{"name":"llama3.2:1b"}]}'
        )
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="qwen2.5:0.5b")

        self.assertEqual(status["installed"], True)
        self.assertEqual(status["running"], True)
        self.assertEqual(status["model_present"], True)
        self.assertEqual(status["models"], ["qwen2.5:0.5b", "llama3.2:1b"])

    def test_status_includes_installed_and_recommended_model_options(self) -> None:
        from evalhub.ollama import get_ollama_status

        response = _Response(b'{"models":[{"name":"qwen2.5:0.5b"}]}')
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="llama3.2:1b")

        options = status["model_options"]
        option_by_name = {option["name"]: option for option in options}
        self.assertEqual(option_by_name["qwen2.5:0.5b"]["installed"], True)
        self.assertEqual(option_by_name["llama3.2:1b"]["installed"], False)
        self.assertIn("轻量", option_by_name["llama3.2:1b"]["description"])

    def test_status_reports_not_running_when_api_unreachable(self) -> None:
        from evalhub.ollama import get_ollama_status

        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", side_effect=URLError("connection refused")),
        ):
            status = get_ollama_status(model="qwen2.5:0.5b")

        self.assertEqual(status["installed"], True)
        self.assertEqual(status["running"], False)
        self.assertEqual(status["model_present"], False)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


if __name__ == "__main__":
    unittest.main()
