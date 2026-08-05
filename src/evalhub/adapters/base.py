"""声明所有模型推理后端必须遵循的统一适配器接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelGeneration:
    """保存可评分文本及模型后端提供的完成诊断。"""

    text: str
    done: bool
    done_reason: str
    output_tokens: int | None = None


class ModelGenerationError(RuntimeError):
    """表示模型没有产出可评分文本，并携带稳定错误代码。"""

    def __init__(self, code: str, message: str) -> None:
        """保存跨进程可传递的生成错误分类和安全说明。

        Args:
            code: 调度层识别阻塞原因的稳定错误代码。
            message: 可安全展示给任务详情的诊断文本。
        """
        super().__init__(message)
        self.code = code


class ModelAdapter(ABC):
    """统一本地模型、托管推理服务和远程 API 的文本生成能力。"""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: object) -> str | ModelGeneration:
        """根据输入提示词和运行参数生成模型文本。

        Args:
            prompt: 发送给模型的完整文本输入。
            **kwargs: 具体后端支持的可选推理参数。

        Returns:
            模型后端返回的文本预测，或带完成诊断的结构化生成结果。
        """
        # 抽象基类显式失败，防止未实现生成能力的子类被误用于评测流程。
        raise NotImplementedError


def unpack_model_generation(
    generation: str | ModelGeneration,
) -> tuple[str, dict[str, object]]:
    """把新旧适配器输出统一为预测文本和可持久化诊断。

    Args:
        generation: 旧适配器返回的字符串，或带完成原因的新结果。

    Returns:
        原始预测文本和只包含后端完成事实的 metadata 字典。

    Raises:
        TypeError: 适配器返回了协议之外的对象。
    """
    if isinstance(generation, str):
        return generation, {}
    if not isinstance(generation, ModelGeneration):
        raise TypeError(f"unexpected model generation type: {type(generation).__name__}")
    # 字段名带 generation 前缀，避免覆盖数据集自身的样本 metadata。
    metadata: dict[str, object] = {
        "generation_done": generation.done,
        "generation_done_reason": generation.done_reason,
    }
    if generation.output_tokens is not None:
        metadata["generation_output_tokens"] = generation.output_tokens
    return generation.text, metadata
