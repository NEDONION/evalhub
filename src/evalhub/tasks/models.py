"""定义本地任务中心使用的请求、资源和持久化任务记录。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal

TaskStatus = Literal["pending", "running", "success", "failed", "canceled"]
EvaluationType = Literal["model", "agent"]
NodeStatus = Literal["pending", "running", "success", "failed", "blocked", "canceled"]
SampleStatus = Literal["success", "failed"]


@dataclass(frozen=True)
class TaskRequest:
    """保存一次模型或 Agent 评测任务运行所需的完整请求。"""

    # 这些字段与前端评测表单和同步 API 保持一致，便于任务恢复后原样执行。
    dataset: str
    adapter: str
    model: str
    base_url: str
    sample_mode: str
    subject: str
    limit: int | None
    # 新字段放在末尾并提供兼容默认值，使旧 API 和已有 SQLite JSON 仍可恢复。
    evaluation_type: EvaluationType = "model"
    agent_framework: str | None = None
    suite_id: str | None = None
    agent_difficulty: str | None = None
    generation_config: dict[str, object] | None = None
    evaluator_type: str | None = None


@dataclass(frozen=True)
class ResourceUsage:
    """表示单次采样得到的评测进程资源占用。"""

    # CPU 可以超过 100% 表示多核占用，内存和显存统一使用字节保存。
    cpu_percent: float = 0.0
    memory_bytes: int = 0
    gpu_supported: bool = False
    gpu_percent: float | None = None
    gpu_memory_bytes: int | None = None


@dataclass(frozen=True)
class EvaluationTask:
    """表示从 SQLite 恢复的完整评测任务状态快照。"""

    # 身份、请求和生命周期字段共同描述可恢复的任务执行状态。
    id: str
    request: TaskRequest
    status: TaskStatus
    completed_samples: int
    total_samples: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    # 当前值支持运行态实时展示，峰值保留终态任务的资源上界。
    cpu_percent: float = 0.0
    peak_cpu_percent: float = 0.0
    memory_bytes: int = 0
    peak_memory_bytes: int = 0
    gpu_supported: bool = False
    gpu_percent: float | None = None
    peak_gpu_percent: float | None = None
    gpu_memory_bytes: int | None = None
    peak_gpu_memory_bytes: int | None = None
    # 结果摘要单独存列供列表读取，完整正文只在详情查询时加载。
    benchmark: str | None = None
    passed_samples: int | None = None
    average_score: float | None = None
    comparison_fingerprint: str | None = None
    result: dict[str, object] | None = None


def _empty_mapping() -> Mapping[str, object]:
    """返回工作流规格可安全共享的空只读映射。"""
    return MappingProxyType({})


@dataclass(frozen=True)
class WorkflowNodeSpec:
    """描述创建评测任务时需要持久化的一个固定流程节点。"""

    node_key: str
    kind: str
    depends_on: tuple[str, ...] = ()
    input: Mapping[str, object] = field(default_factory=_empty_mapping)
    max_attempts: int = 3


@dataclass(frozen=True)
class EvaluationNode:
    """表示从 SQLite 恢复的节点最新状态快照。"""

    id: str
    task_id: str
    node_key: str
    kind: str
    depends_on: tuple[str, ...]
    status: NodeStatus
    attempt_count: int
    max_attempts: int
    input: dict[str, object]
    checkpoint: dict[str, object] | None
    output: dict[str, object] | None
    error_type: str | None
    error_message: str | None
    completed_samples: int
    total_samples: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    attempt_started_at: datetime | None
    finished_at: datetime | None
    elapsed_ms: int


@dataclass(frozen=True)
class EvaluationNodeEvent:
    """表示节点一次不可变的状态、检查点或诊断审计事件。"""

    id: int
    task_id: str
    node_id: str
    event_type: str
    from_status: NodeStatus | None
    to_status: NodeStatus | None
    attempt: int
    actor: str
    message: str | None
    payload: dict[str, object] | None
    created_at: datetime


@dataclass(frozen=True)
class EvaluationSampleCheckpoint:
    """保存 Benchmark 节点中一个样本的最新可恢复结果。"""

    node_id: str
    sample_key: str
    sample_index: int
    status: SampleStatus
    attempt_count: int
    input: dict[str, object]
    result: dict[str, object] | None = None
    last_error: dict[str, object] | None = None
    task_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class EvaluationSamplePage:
    """表示稳定游标分页返回的一页样本检查点。"""

    items: tuple[EvaluationSampleCheckpoint, ...]
    next_cursor: str | None
