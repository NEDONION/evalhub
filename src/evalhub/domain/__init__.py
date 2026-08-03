"""集中导出 EvalHub 领域层的稳定实体与枚举接口。"""

from evalhub.domain.entities import (
    BenchmarkRecord,
    DatasetRecord,
    EvaluationJob,
    EvaluationReport,
    EvaluationSample,
    EvaluationSampleResult,
    MetricResult,
    ModelRecord,
)
from evalhub.domain.enums import JobStatus, ModelType

# 明确公开符号，避免调用方依赖领域包中的实现细节。
__all__ = [
    "BenchmarkRecord",
    "DatasetRecord",
    "EvaluationJob",
    "EvaluationReport",
    "EvaluationSample",
    "EvaluationSampleResult",
    "JobStatus",
    "MetricResult",
    "ModelRecord",
    "ModelType",
]
