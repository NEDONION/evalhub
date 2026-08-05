"""声明控制台模型在正式 Benchmark 中使用的 Ollama 生成协议。"""

from collections.abc import Mapping
from dataclasses import dataclass

MODEL_GENERATION_PROTOCOL_VERSION = "ollama-generate-v1"


@dataclass(frozen=True)
class ModelGenerationProfile:
    """描述一个精确模型标签所需的 Ollama 顶层生成参数。"""

    name: str
    protocol_version: str = MODEL_GENERATION_PROTOCOL_VERSION
    think: bool | None = None


_THINKING_MODELS = frozenset(
    {
        "qwen3:4b",
        "qwen3:8b",
        "qwen3:14b",
        "gemma4:12b",
        "lfm2.5:8b",
        "north-mini-code-1.0:q4_K_M",
        "deepseek-r1:1.5b",
    }
)
_REGISTERED_MODELS = (
    "granite4.1:3b",
    "granite3.3:8b",
    "qwen3:4b",
    "qwen3:8b",
    "qwen3:14b",
    "ministral-3:8b",
    "gemma4:12b",
    "lfm2.5:8b",
    "north-mini-code-1.0:q4_K_M",
    "qwen2.5:0.5b",
    "qwen2.5:1.5b",
    "deepseek-r1:1.5b",
    "qwen2.5-coder:7b",
)
_PROFILES = {
    name: ModelGenerationProfile(
        name=name,
        think=False if name in _THINKING_MODELS else None,
    )
    for name in _REGISTERED_MODELS
}


def model_generation_profiles() -> dict[str, ModelGenerationProfile]:
    """返回当前模型标签到不可变生成协议的独立映射。

    Returns:
        新字典；其中每个 profile 自身不可变，调用方修改字典不会污染全局协议。
    """
    return dict(_PROFILES)


def get_model_generation_profile(model: str) -> ModelGenerationProfile:
    """读取精确模型标签对应的生成协议。

    Args:
        model: Ollama 使用的完整模型标签。

    Returns:
        已登记且不可变的模型生成协议。

    Raises:
        ValueError: 标签未登记，不能安全用于正式 Benchmark。
    """
    try:
        return _PROFILES[model]
    except KeyError as exc:
        # 未知模型不按名称猜测能力，避免静默发送不兼容的思考参数。
        raise ValueError(f"model_protocol_not_registered: {model}") from exc


def effective_generation_config(
    model: str, benchmark_config: Mapping[str, object]
) -> dict[str, object]:
    """把模型传输协议合并到 Benchmark 自身的生成预算。

    Args:
        model: 需要执行正式 Benchmark 的精确 Ollama 模型标签。
        benchmark_config: Benchmark 决定的采样参数和最大输出长度。

    Returns:
        可直接传给模型适配器的新配置，不修改调用方映射。

    Raises:
        ValueError: 模型没有登记生成协议。
    """
    profile = get_model_generation_profile(model)
    config = dict(benchmark_config)
    if profile.think is not None:
        # 思考开关属于 Ollama 请求顶层，仍随有效配置一起被冻结和纳入协议指纹。
        config["think"] = profile.think
    return config
