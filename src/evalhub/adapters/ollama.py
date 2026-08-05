"""通过 Ollama 本地 HTTP API 调用模型并统一转换连接与响应错误。"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evalhub.adapters.base import ModelAdapter, ModelGeneration, ModelGenerationError


class OllamaAdapter(ModelAdapter):
    """把 EvalHub 文本生成接口适配到本地 Ollama 服务。

    使用前需要运行 ``ollama serve``，并通过 ``ollama pull`` 准备目标模型。
    """

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434") -> None:
        """配置固定模型名称并规范化 Ollama 服务根地址。

        Args:
            model: Ollama 本地已安装或准备拉取的模型标签。
            base_url: Ollama HTTP 服务根地址，末尾斜杠会被移除。
        """
        # 模型由适配器实例固定，确保同一评测任务不会跨模型混用结果。
        self.model = model
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, **kwargs: object) -> ModelGeneration:
        """调用 Ollama 非流式生成接口并返回文本及完成诊断。

        Args:
            prompt: 发送给本地模型的完整输入文本。
            **kwargs: 可选的温度、采样概率、生成长度和随机种子参数。

        Returns:
            Ollama 响应中的完整文本、完成状态、终止原因和可选 token 数。

        Raises:
            ModelGenerationError: 服务没有返回任何可评分文本。
            RuntimeError: HTTP 请求失败、服务不可达或响应字段类型不符合协议。
            ValueError: 调用方提供的 ``think`` 不是布尔值。
        """
        # 只透传 Ollama 明确支持的运行参数，避免 Benchmark 配置意外污染请求体。
        options = {
            key: value
            for key, value in kwargs.items()
            if key in {"temperature", "top_p", "num_predict", "seed"}
        }
        # 禁用流式响应，使一次调用与一个样本结果形成清晰的一一对应关系。
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        think = kwargs.get("think")
        if think is not None:
            if type(think) is not bool:
                raise ValueError("Ollama think must be a boolean")
            # Ollama 的思考开关属于请求顶层，不是 options 中的采样参数。
            payload["think"] = think
        # 请求对象集中声明编码、内容类型和方法，便于在网络边界统一测试替换。
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            # 本地大模型推理可能耗时较长，因此使用适合完整生成的五分钟超时。
            with urlopen(request, timeout=300) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # 优先解析 Ollama 返回的结构化错误，向用户呈现比 HTTP 原因更具体的信息。
            detail = exc.reason
            if exc.fp is not None:
                raw_body = exc.fp.read().decode("utf-8", errors="replace")
                try:
                    # 服务通常把错误放在 ``error`` 字段，未知结构则保留原始响应正文。
                    parsed_body = json.loads(raw_body)
                    detail = parsed_body.get("error", raw_body)
                except json.JSONDecodeError:
                    # 非 JSON 错误页仍具有诊断价值，空正文时才退回标准 HTTP 原因。
                    detail = raw_body or exc.reason
            # 保留 HTTP 异常为因果链，便于上层日志获得状态码之外的网络上下文。
            raise RuntimeError(
                f"Ollama 推理失败：HTTP {exc.code}。{detail}"
            ) from exc
        except URLError as exc:
            # 连接失败时给出可执行的本地服务和模型准备指令，减少排障往返。
            raise RuntimeError(
                f"无法连接 Ollama 服务：{self.base_url}。请先安装并启动 Ollama，"
                f"然后执行：ollama pull {self.model}"
            ) from exc

        # 三个完成字段共同构成非流式响应边界，拒绝字符串强转掩盖服务协议变化。
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("response"), str)
            or type(body.get("done")) is not bool
            or not isinstance(body.get("done_reason"), str)
        ):
            raise RuntimeError(f"unexpected Ollama response: {body}")
        text = body["response"]
        done_reason = body["done_reason"]
        if not text.strip():
            # 长度耗尽通常表示思考占满预算；其他空停止仍是不可评分的模型响应。
            code = (
                "generation_incomplete"
                if done_reason == "length"
                else "empty_model_response"
            )
            raise ModelGenerationError(code, f"{code}: Ollama 未返回可评分的最终回答")
        output_tokens = body.get("eval_count")
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
            output_tokens = None
        # 非空的 length 响应仍可正常评分，同时保留终止原因供账本和排障使用。
        return ModelGeneration(
            text=text,
            done=body["done"],
            done_reason=done_reason,
            output_tokens=output_tokens,
        )
