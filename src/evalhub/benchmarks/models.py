"""定义 Benchmark Registry 使用的不可变领域模型。"""

from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    """标识 Benchmark 归属的六个稳定能力维度。"""

    KNOWLEDGE = "knowledge"
    INSTRUCTION_FOLLOWING = "instruction_following"
    MATHEMATICS = "mathematics"
    REASONING = "reasoning"
    CODING = "coding"
    SAFETY_TRUST = "safety_trust"


class ExecutorKind(StrEnum):
    """标识运行 Benchmark 所需的执行器类型。"""

    NATIVE = "native"
    LM_EVAL = "lm_eval"
    SANDBOXED_CODE = "sandboxed_code"


class NormalizationKind(StrEnum):
    """标识原始指标转换为统一百分制时采用的规则。"""

    SCALE_100 = "scale_100"
    CHANCE_CORRECTED = "chance_corrected"


@dataclass(frozen=True)
class BenchmarkSpec:
    """描述一个带有可复现协议元数据的版本化 Benchmark。"""

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
    few_shot: int = 0
    generation_config: dict[str, object] = field(
        default_factory=lambda: {"temperature": 0, "num_predict": 256}
    )
    requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkSuiteSpec:
    """描述由固定顺序 Benchmark ID 组成的版本化评测套件。"""

    id: str
    version: str
    display_name: str
    benchmark_ids: tuple[str, ...]
