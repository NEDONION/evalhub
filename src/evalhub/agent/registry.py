"""注册 EvalHub 允许执行的完整 Agent，并绑定其运行配置。"""

from pathlib import Path

from evalhub.agent.base import (
    AgentDefinition,
    AgentMetadata,
    AgentRunner,
    AgentRunResult,
    AgentStatus,
    TraceCallback,
)
from evalhub.agent.pi import PiAgentRunner

_DEFINITIONS = (
    AgentDefinition(
        id="pi",
        name="Pi CLI",
        description="由 EvalHub 选择模型的编码 Agent",
        model_mode="evalhub",
    ),
    AgentDefinition(
        id="miniclaw",
        name="MiniClaw",
        description="使用自身模型、工具、策略与记忆的完整 Agent",
        model_mode="agent",
    ),
)


class _ConfiguredPiRunner:
    """把现有 Pi 模型参数绑定为通用完整 Agent Runner。"""

    def __init__(self, runner: PiAgentRunner, *, model: str, base_url: str) -> None:
        """保存 Pi 执行器和当前任务冻结的模型连接参数。

        Args:
            runner: 继续负责 Pi 沙箱、代理和 JSONL 的既有执行器。
            model: EvalHub 为这次 Pi 评测选择的模型 ID。
            base_url: 对应 Ollama 或 API Provider 的冻结地址。
        """
        self._runner = runner
        self._model = model
        self._base_url = base_url

    def metadata(self) -> AgentMetadata:
        """返回 Pi 完整候选所用版本与模型，供结果和排行审计。"""
        return AgentMetadata("pi", "Pi CLI", self._runner.version(), self._model)

    def run(
        self,
        *,
        instruction: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> AgentRunResult:
        """把通用样本请求转交给已绑定模型的 Pi 执行器。

        Args:
            instruction: Coding Mini 的公开任务说明。
            workspace: 当前样本唯一可写的 Git 工作区。
            timeout_seconds: 样本运行上限。
            on_event: 接收 Pi 标准化事件的可选回调。

        Returns:
            Pi 产生的通用运行结果。
        """
        return self._runner.run(
            instruction=instruction,
            model=self._model,
            base_url=self._base_url,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            on_event=on_event,
        )


def agent_definitions() -> tuple[AgentDefinition, ...]:
    """返回固定顺序的完整 Agent 目录，不访问文件或外部服务。"""
    return _DEFINITIONS


def _definition(agent_id: str) -> AgentDefinition:
    """解析稳定 Agent ID，并在未知值进入任务前明确拒绝。

    Args:
        agent_id: API 或任务记录提供的完整 Agent ID。

    Returns:
        对应的受控 Agent 定义。

    Raises:
        ValueError: ID 未在静态 Registry 中登记。
    """
    for definition in _DEFINITIONS:
        if definition.id == agent_id:
            return definition
    raise ValueError(f"unknown agent: {agent_id}")


def create_agent_runner(
    agent_id: str,
    *,
    adapter: str = "ollama",
    model: str = "",
    base_url: str = "",
    provider_id: str | None = None,
    api_key: str | None = None,
    miniclaw_root: Path | None = None,
) -> AgentRunner:
    """按受控 ID 构造完整 Agent Runner，并绑定本次任务配置。

    Args:
        agent_id: Registry 中的完整 Agent ID。
        adapter: 只供 Pi 使用的模型适配器类型。
        model: 只供 Pi 使用的模型 ID。
        base_url: 只供 Pi 使用的模型服务地址。
        provider_id: Pi API 模式的固定 Provider ID。
        api_key: Pi 受控代理在 Worker 内短暂解析的凭据。
        miniclaw_root: 测试或部署显式指定的 MiniClaw 项目根目录。

    Returns:
        已准备接收 Coding Mini 样本的通用 Runner。

    Raises:
        ValueError: Agent ID 未登记。
    """
    _definition(agent_id)
    if agent_id == "pi":
        runner = PiAgentRunner(
            adapter=adapter,
            provider_id=provider_id,
            api_key=api_key,
        )
        return _ConfiguredPiRunner(runner, model=model, base_url=base_url)

    # MiniClaw 延迟导入，避免 Registry 导入阶段探测外部项目或 Python 环境。
    from evalhub.agent.miniclaw import MiniClawAgentRunner

    return MiniClawAgentRunner(root=miniclaw_root)


def agent_statuses() -> tuple[AgentStatus, ...]:
    """读取所有受控 Agent 的本机就绪状态，不返回任何凭据内容。

    Returns:
        与 Registry 相同顺序的状态快照。
    """
    statuses: list[AgentStatus] = []
    for definition in _DEFINITIONS:
        statuses.append(_agent_status(definition))
    return tuple(statuses)


def _agent_status(definition: AgentDefinition) -> AgentStatus:
    """把单个 Agent 的探测结果收窄为统一安全对象。

    Args:
        definition: 当前需要探测的受控 Agent 定义。

    Returns:
        可直接进入 API 的非敏感状态。
    """
    try:
        runner = create_agent_runner(definition.id)
        metadata = runner.metadata()
    except (OSError, RuntimeError, ValueError) as exc:
        message = str(exc).strip() or f"{definition.name} is unavailable"
        return AgentStatus(
            definition.id,
            definition.name,
            definition.description,
            definition.model_mode,
            False,
            None,
            None,
            message,
        )

    return AgentStatus(
        definition.id,
        definition.name,
        definition.description,
        definition.model_mode,
        True,
        metadata.version,
        metadata.model,
        "ready",
    )
