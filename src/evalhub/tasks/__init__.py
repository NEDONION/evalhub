"""公开本地评测任务模型与 SQLite 仓储接口。"""

from evalhub.tasks.executor import TaskExecutionCanceled
from evalhub.tasks.models import (
    EvaluationNode,
    EvaluationNodeEvent,
    EvaluationSampleCheckpoint,
    EvaluationSamplePage,
    EvaluationTask,
    EvaluationType,
    NodeStatus,
    ResourceUsage,
    TaskRequest,
    TaskStatus,
    WorkflowNodeSpec,
)
from evalhub.tasks.repository import SQLiteTaskRepository, TaskNotFoundError, TaskStateError
from evalhub.tasks.service import EvaluationTaskService, TaskConflictError

# 任务包只导出稳定的调度边界，数据库行映射等实现细节保持私有。
__all__ = [
    "EvaluationNode",
    "EvaluationNodeEvent",
    "EvaluationSampleCheckpoint",
    "EvaluationSamplePage",
    "EvaluationTask",
    "EvaluationTaskService",
    "EvaluationType",
    "NodeStatus",
    "ResourceUsage",
    "SQLiteTaskRepository",
    "TaskNotFoundError",
    "TaskConflictError",
    "TaskExecutionCanceled",
    "TaskRequest",
    "TaskStateError",
    "TaskStatus",
    "WorkflowNodeSpec",
]
