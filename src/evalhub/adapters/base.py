"""声明所有模型推理后端必须遵循的统一适配器接口。"""

from abc import ABC, abstractmethod


class ModelAdapter(ABC):
    """统一本地模型、托管推理服务和远程 API 的文本生成能力。"""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: object) -> str:
        """根据输入提示词和运行参数生成模型文本。

        Args:
            prompt: 发送给模型的完整文本输入。
            **kwargs: 具体后端支持的可选推理参数。

        Returns:
            模型后端返回的文本预测。
        """
        # 抽象基类显式失败，防止未实现生成能力的子类被误用于评测流程。
        raise NotImplementedError
