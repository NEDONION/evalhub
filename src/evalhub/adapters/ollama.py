import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from evalhub.adapters.base import ModelAdapter


class OllamaAdapter(ModelAdapter):
    """Adapter for a local Ollama server.

    Expected local service:
        ollama serve
        ollama pull qwen2.5:0.5b
    """

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, **kwargs: object) -> str:
        options = {
            key: value
            for key, value in kwargs.items()
            if key in {"temperature", "top_p", "num_predict", "seed"}
        }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=300) as response:
                body = json.loads(response.read().decode("utf-8"))
        except URLError as exc:
            raise RuntimeError(
                f"无法连接 Ollama 服务：{self.base_url}。请先安装并启动 Ollama，"
                f"然后执行：ollama pull {self.model}"
            ) from exc

        if "response" not in body:
            raise RuntimeError(f"unexpected Ollama response: {body}")
        return str(body["response"])
