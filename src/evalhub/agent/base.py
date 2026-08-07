"""定义完整 Agent 评测共享的运行契约和公开元数据。"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict


class AgentRunError(RuntimeError):
    """表示完整 Agent 未能产生可评分的执行结果。"""

    def __init__(self, message: str, *, error_type: str | None = None) -> None:
        """保存可审计的安全错误和可选基础设施分类。

        Args:
            message: 可以进入 EvalHub 任务事件的脱敏说明。
            error_type: 仅确定性执行器故障使用的稳定分类。
        """
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class AgentRunResult:
    """保存一次 Agent 运行需要进入 Benchmark 的最小外部事实。"""

    final_message: str
    event_count: int
    return_code: int
    wall_time_seconds: float
    cli_version: str
    tool_call_count: int = 0

    @property
    def version(self) -> str:
        """返回通用 Agent 版本，并兼容既有 ``cli_version`` 字段。"""
        return self.cli_version


class AgentTraceEvent(TypedDict):
    """描述可以跨进程持久化的单条 Agent 白名单事件。"""

    event_type: str
    actor: str
    message: str | None
    payload: dict[str, object]


TraceCallback = Callable[[AgentTraceEvent], None]


@dataclass(frozen=True)
class AgentMetadata:
    """描述完整 Agent 的稳定身份和非敏感运行快照。"""

    framework: str
    name: str
    version: str
    model: str | None
    runtime_fingerprint: str | None = None


class AgentRunner(Protocol):
    """定义 Coding Mini 调用完整 Agent 所需的最小同步边界。"""

    def metadata(self) -> AgentMetadata:
        """返回本次 Runner 使用的完整 Agent 身份快照。"""

    def run(
        self,
        *,
        instruction: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> AgentRunResult:
        """在指定样本工作区执行公开任务，并返回外部可观察结果。"""


@dataclass(frozen=True)
class AgentDefinition:
    """描述控制台可选择的一个受控完整 Agent。"""

    id: str
    name: str
    description: str
    model_mode: str


@dataclass(frozen=True)
class AgentStatus:
    """描述一个 Agent 在当前机器上的非敏感就绪状态。"""

    id: str
    name: str
    description: str
    model_mode: str
    available: bool
    version: str | None
    model: str | None
    message: str
