"""定义行业 Benchmark Registry 使用的不可变领域模型。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class Capability(StrEnum):
    """标识能力画像使用的六个稳定维度。"""

    KNOWLEDGE = "knowledge"
    INSTRUCTION_FOLLOWING = "instruction_following"
    MATHEMATICS = "mathematics"
    REASONING = "reasoning"
    CODING = "coding"
    SAFETY_TRUST = "safety_trust"


class ExecutorKind(StrEnum):
    """标识运行 Benchmark 所需的执行器边界。"""

    NATIVE = "native"
    LM_EVAL = "lm_eval"
    SANDBOXED_CODE = "sandboxed_code"


class NormalizationKind(StrEnum):
    """标识原始指标转换为百分制时采用的规则。"""

    SCALE_100 = "scale_100"
    CHANCE_CORRECTED = "chance_corrected"


def _default_generation_config() -> Mapping[str, object]:
    """返回不能被调用方原地修改的确定性生成参数。"""
    return MappingProxyType({"temperature": 0, "num_predict": 256})


@dataclass(frozen=True)
class BenchmarkSpec:
    """描述一个带可复现协议元数据的版本化 Benchmark。"""

    id: str
    version: str
    display_name: str
    capability: Capability
    dataset_source: str
    dataset_revision: str
    homepage: str
    license: str
    expected_sample_count: int | None
    executor: ExecutorKind
    task_name: str
    metric: str
    normalization: NormalizationKind
    random_baseline: float | None = None
    weight: float = 1.0
    prompt_template_version: str = "evalhub-v1"
    answer_protocol_version: str = "legacy-answer-v1"
    few_shot: int = 0
    generation_config: Mapping[str, object] = field(default_factory=_default_generation_config)
    requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkSuiteSpec:
    """描述由固定顺序 Benchmark ID 组成的版本化评测套件。"""

    id: str
    version: str
    display_name: str
    benchmark_ids: tuple[str, ...]
