"""定义评测平台核心领域实体、标识生成与 UTC 时间工具。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from evalhub.domain.enums import JobStatus, ModelType


def new_id(prefix: str) -> str:
    """生成带领域前缀的短随机标识。

    Args:
        prefix: 表示实体类型的稳定前缀，例如 ``model`` 或 ``job``。

    Returns:
        由前缀和 12 位 UUID 十六进制片段组成的标识。
    """
    # 保留实体类型前缀，既方便日志排查，也降低跨表误用标识的概率。
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    """返回带 UTC 时区信息的当前时间，供实体默认字段统一使用。"""
    # 使用显式 UTC 时区，避免本地时区差异破坏任务耗时和排序计算。
    return datetime.now(UTC)


@dataclass(frozen=True)
class ModelRecord:
    """记录可评测模型的版本、来源类型及可选访问位置。"""

    # 名称、版本和类型共同描述模型身份，连接信息根据模型形态按需提供。
    name: str
    version: str
    type: ModelType
    endpoint: str | None = None
    checkpoint_path: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    # 标识与创建时间由领域层生成，保证不同入口创建记录时行为一致。
    id: str = field(default_factory=lambda: new_id("model"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class DatasetRecord:
    """记录数据集版本、存储位置、结构定义及规模等注册信息。"""

    # 这些字段共同支持数据集复现、责任归属和加载前的结构校验。
    name: str
    version: str
    storage_uri: str
    schema: dict[str, object]
    owner: str
    sample_count: int
    # 数据集记录不可变，标识和时间用于后续 Benchmark 稳定引用与审计。
    id: str = field(default_factory=lambda: new_id("dataset"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class BenchmarkRecord:
    """描述数据集、评测器和运行配置组成的可复现评测基准。"""

    # Benchmark 只保存关联标识与配置，不直接持有可能很大的样本集合。
    name: str
    dataset_id: str
    evaluator_type: str
    config: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("benchmark"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class EvaluationJob:
    """维护一次模型评测任务的配置、状态、时间和失败信息。"""

    # 模型与 Benchmark 标识决定评测对象，运行配置用于覆盖基准默认参数。
    model_id: str
    benchmark_id: str
    runtime_config: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("job"))
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None

    def mark_running(self) -> None:
        """把待执行任务切换为运行态并记录实际开始时间。"""
        # 状态与时间必须同时更新，避免观察者看到缺少开始时间的运行任务。
        self.status = JobStatus.RUNNING
        self.started_at = utc_now()

    def mark_success(self) -> None:
        """把任务标记为成功终态并记录完成时间。"""
        # 成功状态和完成时间一起落位，便于后续计算端到端执行耗时。
        self.status = JobStatus.SUCCESS
        self.finished_at = utc_now()

    def mark_failed(self, message: str) -> None:
        """把任务标记为失败终态并保存可诊断的错误信息。

        Args:
            message: 面向任务记录的失败原因，不应包含密钥等敏感数据。
        """
        # 先记录终态与原因，再统一写入结束时间，保证失败记录信息完整。
        self.status = JobStatus.FAILED
        self.error_message = message
        self.finished_at = utc_now()


@dataclass(frozen=True)
class EvaluationSample:
    """表示送入模型的单条输入、参考答案及辅助元数据。"""

    # 输入与参考答案用于推理和评分，元数据保留数据集特有的上下文。
    input: str
    reference: str
    id: str = field(default_factory=lambda: new_id("sample"))
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricResult:
    """表示评测器对单条预测给出的指标得分与解释。"""

    # 指标名和数值用于聚合，原因与元数据用于报告解释和问题定位。
    metric: str
    score: float
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationSampleResult:
    """保存一次任务中单条样本的预测、评分及可追踪关联。"""

    # 任务和样本标识建立追踪关系，正文快照保证报告可独立复现。
    job_id: str
    sample_id: str
    input: str
    prediction: str
    reference: str
    metric: str
    score: float
    reason: str | None = None
    # 结果使用独立标识和 UTC 创建时间，支持后续持久化与排序。
    id: str = field(default_factory=lambda: new_id("result"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EvaluationReport:
    """汇总评测任务的样本规模、通过数量、均分和失败样本。"""

    # 聚合字段面向报告与门禁消费，失败标识列表保留回溯到样本的入口。
    job_id: str
    metric: str
    total_samples: int
    passed_samples: int
    average_score: float
    failed_sample_ids: list[str]
    created_at: datetime = field(default_factory=utc_now)
