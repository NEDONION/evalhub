"""提供不依赖外部服务的确定性静态映射模型适配器。"""

from evalhub.adapters.base import ModelAdapter


class StaticMappingAdapter(ModelAdapter):
    """为演示、测试和管线校验按输入返回预设的确定性响应。"""

    def __init__(self, responses: dict[str, str], default_response: str = "") -> None:
        """保存输入响应映射与未命中时使用的默认文本。

        Args:
            responses: 以完整提示词为键、模型响应为值的映射。
            default_response: 提示词未配置时返回的兜底文本。
        """
        # 直接保存映射以保持调用开销最小，调用方负责管理其后续可变性。
        self._responses = responses
        self._default_response = default_response

    def generate(self, prompt: str, **kwargs: object) -> str:
        """返回提示词对应的预设响应，未命中时返回默认文本。

        Args:
            prompt: 用于查找静态响应的完整提示词。
            **kwargs: 为兼容统一接口而接收但不参与静态查找的参数。

        Returns:
            映射命中的响应或构造时配置的默认响应。
        """
        # 静态适配器忽略推理参数，确保相同提示词始终得到完全一致的结果。
        return self._responses.get(prompt, self._default_response)
