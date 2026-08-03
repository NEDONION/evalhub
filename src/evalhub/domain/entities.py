from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from evalhub.domain.enums import JobStatus, ModelType


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ModelRecord:
    name: str
    version: str
    type: ModelType
    endpoint: str | None = None
    checkpoint_path: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("model"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class DatasetRecord:
    name: str
    version: str
    storage_uri: str
    schema: dict[str, object]
    owner: str
    sample_count: int
    id: str = field(default_factory=lambda: new_id("dataset"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class BenchmarkRecord:
    name: str
    dataset_id: str
    evaluator_type: str
    config: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("benchmark"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class EvaluationJob:
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
        self.status = JobStatus.RUNNING
        self.started_at = utc_now()

    def mark_success(self) -> None:
        self.status = JobStatus.SUCCESS
        self.finished_at = utc_now()

    def mark_failed(self, message: str) -> None:
        self.status = JobStatus.FAILED
        self.error_message = message
        self.finished_at = utc_now()


@dataclass(frozen=True)
class EvaluationSample:
    input: str
    reference: str
    id: str = field(default_factory=lambda: new_id("sample"))
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricResult:
    metric: str
    score: float
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationSampleResult:
    job_id: str
    sample_id: str
    input: str
    prediction: str
    reference: str
    metric: str
    score: float
    reason: str | None = None
    id: str = field(default_factory=lambda: new_id("result"))
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EvaluationReport:
    job_id: str
    metric: str
    total_samples: int
    passed_samples: int
    average_score: float
    failed_sample_ids: list[str]
    created_at: datetime = field(default_factory=utc_now)
