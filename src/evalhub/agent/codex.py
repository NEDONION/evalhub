"""运行固定 Codex CLI Agent 壳并提取可评分结果。"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import IO, Protocol, TypedDict


class CodexAgentError(RuntimeError):
    """表示 Codex CLI 未能产生可评分的 Agent 执行结果。"""


@dataclass(frozen=True)
class CodexRunResult:
    """记录一次固定 Agent 壳运行后需要持久化的审计信息。"""

    final_message: str
    event_count: int
    return_code: int
    wall_time_seconds: float
    cli_version: str
    tool_call_count: int = 0


class AgentTraceEvent(TypedDict):
    """描述可跨进程持久化的单条 Codex 外部可观察事件。"""

    event_type: str
    actor: str
    message: str | None
    payload: dict[str, object]


TraceCallback = Callable[[AgentTraceEvent], None]


class CommandRunner(Protocol):
    """描述可替换的子进程执行函数，便于测试时隔离真实 Codex。"""

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """执行命令并返回已完成进程；关键字参数遵循 ``subprocess.run``。"""


class StreamingProcess(Protocol):
    """描述流式 Codex 子进程需要暴露的最小生命周期接口。"""

    stdout: IO[str] | None
    stderr: IO[str] | None
    returncode: int | None

    def poll(self) -> int | None:
        """返回当前退出码；进程仍运行时返回空值。"""

    def wait(self, timeout: float | None = None) -> int:
        """等待进程结束并返回退出码。"""

    def terminate(self) -> None:
        """请求进程优雅终止。"""

    def kill(self) -> None:
        """强制终止未响应的进程。"""


class ProcessFactory(Protocol):
    """描述可注入的 Codex 流式进程构造器。"""

    def __call__(self, command: list[str], **kwargs: object) -> StreamingProcess:
        """启动命令并返回可读取 stdout/stderr 的进程。"""


class CodexAgentRunner:
    """通过受约束的 Codex CLI 命令运行本地 Ollama 模型。"""

    def __init__(
        self,
        *,
        run_command: CommandRunner = subprocess.run,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        """注入版本探测和流式进程边界。

        参数：
            run_command: 用于轻量 CLI 版本探测的同步执行器。
            process_factory: 用于真实 Agent 运行的流式进程构造器；生产默认 ``Popen``。
        """
        self._run_command = run_command
        self._process_factory = process_factory or subprocess.Popen
        # 兼容既有只注入 run_command 的调用方；生产默认路径始终使用流式 Popen。
        self._legacy_run_command = process_factory is None and run_command is not subprocess.run
        self._cli_version: str | None = None

    def version(self) -> str:
        """读取并缓存 Codex CLI 版本。

        返回：
            CLI 输出的单行版本文本。

        异常：
            CodexAgentError: CLI 不存在、退出失败或没有返回版本时抛出。
        """
        if self._cli_version is not None:
            return self._cli_version

        # 版本探测沿用同一个可替换执行器，使单元测试不依赖用户机器环境。
        try:
            completed = self._run_command(
                ["codex", "--version"], capture_output=True, text=True, timeout=10, check=False
            )
        except FileNotFoundError as exc:
            raise CodexAgentError("codex CLI is not installed or not available on PATH") from exc

        # 非零退出和空输出都无法形成可复现的 Agent 运行元数据。
        if completed.returncode != 0:
            detail = _error_detail(completed.stderr)
            raise CodexAgentError(f"codex version check failed: {detail}")
        version = completed.stdout.strip()
        if not version:
            raise CodexAgentError("codex version check produced no output")
        self._cli_version = version
        return version

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> CodexRunResult:
        """在指定工作区运行固定 Codex Agent 壳。

        参数：
            instruction: 交给 Agent 的单个编码任务说明。
            model: 通过 Ollama 暴露的基模名称。
            base_url: Ollama 服务地址，会写入子进程 ``OLLAMA_HOST``。
            workspace: Agent 唯一可写的样本工作区。
            timeout_seconds: 本次样本允许的最长执行秒数。
            on_event: 每产生一条白名单外部事件时立即调用的可选回调。

        返回：
            包含最终消息、事件数量、耗时和 CLI 版本的执行结果。

        异常：
            CodexAgentError: 参数无效、CLI 不可用、超时、退出失败或结果缺失时抛出。
        """
        _validate_run_arguments(instruction, model, base_url, workspace, timeout_seconds)
        # Codex 会切换子进程 cwd；提前绝对化可避免状态目录和输出路径被二次相对解析。
        workspace = workspace.resolve()
        cli_version = self.version()

        # 每个样本把 Codex 状态和最终消息限制在自己的工作区，避免污染用户配置。
        evalhub_dir = workspace / ".evalhub"
        codex_home = evalhub_dir / "codex-home"
        output_path = evalhub_dir / "final-message.txt"
        codex_home.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)

        # 命令参数由平台固定，用户只能选择基模和 Ollama 地址。
        command = _build_command(
            instruction=instruction,
            model=model,
            workspace=workspace,
            output_path=output_path,
        )
        environment = os.environ.copy()
        environment["OLLAMA_HOST"] = base_url.rstrip("/")
        environment["CODEX_HOME"] = str(codex_home)

        # 生产路径逐行读取 JSONL；旧测试注入方式仍可同步执行并复用同一标准化逻辑。
        started_at = monotonic()
        try:
            return_code, stdout, stderr = self._execute_command(
                command=command,
                workspace=workspace,
                environment=environment,
                timeout_seconds=timeout_seconds,
                on_event=on_event,
            )
        except FileNotFoundError as exc:
            raise CodexAgentError("codex CLI is not installed or not available on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            timeout_label = f"{timeout_seconds:g}"
            raise CodexAgentError(f"codex timed out after {timeout_label} seconds") from exc
        elapsed_seconds = monotonic() - started_at

        # 只有退出成功且落盘了最终消息才算一次可审计的 Agent 执行。
        if return_code != 0:
            detail = _error_detail(stderr)
            raise CodexAgentError(f"codex exited with code {return_code}: {detail}")
        if not output_path.is_file():
            raise CodexAgentError("codex produced no final message")
        final_message = output_path.read_text(encoding="utf-8").strip()
        if not final_message:
            raise CodexAgentError("codex produced no final message")

        # 流式路径已经实时发送事件；这里仅从完整 stdout 重新计算可审计计数。
        event_count, tool_call_count = _emit_stdout_events(stdout, None)

        return CodexRunResult(
            final_message=final_message,
            event_count=event_count,
            return_code=return_code,
            wall_time_seconds=elapsed_seconds,
            cli_version=cli_version,
            tool_call_count=tool_call_count,
        )

    def _execute_command(
        self,
        *,
        command: list[str],
        workspace: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        on_event: TraceCallback | None,
    ) -> tuple[int, str, str]:
        """执行 Codex 并返回退出码及完整的安全边界输出。

        生产环境从 Popen 流式读取 stdout；兼容路径只服务于既有同步执行器注入。
        """
        if self._legacy_run_command:
            completed = self._run_command(
                command,
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            _emit_stdout_events(completed.stdout, on_event)
            return completed.returncode, completed.stdout, completed.stderr

        # stdout 和 stderr 均使用管道，后台读取线程防止任一管道填满后阻塞 Agent。
        process = self._process_factory(
            command,
            cwd=workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return _consume_process(
            process,
            timeout_seconds=timeout_seconds,
            on_event=on_event,
        )


def _validate_run_arguments(
    instruction: str,
    model: str,
    base_url: str,
    workspace: Path,
    timeout_seconds: float,
) -> None:
    """校验运行参数，避免把明显错误推迟到昂贵的 Agent 子进程中。"""
    if not instruction.strip():
        raise CodexAgentError("instruction must not be empty")
    if not model.strip():
        raise CodexAgentError("model must not be empty")
    if not base_url.strip():
        raise CodexAgentError("base_url must not be empty")

    # 工作区必须由 benchmark 提前创建，Runner 不猜测或扩大可写范围。
    if not workspace.is_dir():
        raise CodexAgentError(f"workspace does not exist: {workspace}")
    if timeout_seconds <= 0:
        raise CodexAgentError("timeout_seconds must be greater than zero")


def _build_command(
    *, instruction: str, model: str, workspace: Path, output_path: Path
) -> list[str]:
    """构造固定且可审计的 Codex 本地 Ollama 命令。"""
    return [
        "codex",
        "exec",
        "--oss",
        "--local-provider",
        "ollama",
        "--model",
        model,
        "--ephemeral",
        "--ignore-user-config",
        "--json",
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(output_path),
        "--cd",
        str(workspace),
        instruction,
    ]


def _consume_process(
    process: StreamingProcess,
    *,
    timeout_seconds: float,
    on_event: TraceCallback | None,
) -> tuple[int, str, str]:
    """并发读取子进程输出，在截止时间内实时发送 stdout 白名单事件。

    参数：
        process: 已启动且 stdout/stderr 配置为文本管道的 Codex 进程。
        timeout_seconds: 整个 Agent 运行允许的最长秒数。
        on_event: 接收标准化事件的可选回调。

    返回：
        进程退出码、完整 stdout 和 stderr。

    异常：
        subprocess.TimeoutExpired: 截止时间到达时终止进程后抛出。
    """
    line_queue: Queue[tuple[str, str | None]] = Queue()
    completed_streams: set[str] = set()

    # 两个读取线程只负责把文本行搬进内存队列，事件解析和回调仍在调用线程串行完成。
    for source, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        if stream is None:
            completed_streams.add(source)
            continue
        Thread(
            target=_read_stream,
            args=(source, stream, line_queue),
            name=f"codex-{source}-reader",
            daemon=True,
        ).start()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    deadline = monotonic() + timeout_seconds
    try:
        # 流结束和进程退出必须同时满足，避免丢失退出前已经写入管道的最后事件。
        while len(completed_streams) < 2 or process.poll() is None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise subprocess.TimeoutExpired("codex exec", timeout_seconds)
            try:
                source, line = line_queue.get(timeout=min(0.1, remaining))
            except Empty:
                continue
            if line is None:
                completed_streams.add(source)
                continue
            if source == "stdout":
                stdout_lines.append(line)
                _emit_stdout_events(line, on_event)
            else:
                stderr_lines.append(line)

        return_code = process.wait(timeout=max(0.0, deadline - monotonic()))
    except BaseException:
        # 回调或读取异常也必须回收 Codex，避免评测 Worker 退出后残留模型客户端。
        if process.poll() is None:
            _stop_process(process)
        raise
    return return_code, "".join(stdout_lines), "".join(stderr_lines)


def _read_stream(
    source: str,
    stream: IO[str],
    line_queue: Queue[tuple[str, str | None]],
) -> None:
    """逐行读取一个进程管道，并用空行哨兵报告该流已经结束。"""
    try:
        for line in stream:
            line_queue.put((source, line))
    finally:
        line_queue.put((source, None))


def _stop_process(process: StreamingProcess) -> None:
    """先请求优雅终止，再强制回收超过两秒仍未退出的 Codex 进程。"""
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _emit_stdout_events(
    stdout: str,
    on_event: TraceCallback | None,
) -> tuple[int, int]:
    """解析一段 JSONL，发送白名单事件并返回对象事件数与工具调用数。"""
    event_count = 0
    tool_call_count = 0
    for line in stdout.splitlines():
        event = _json_object(line)
        if event is None:
            continue
        event_count += 1
        normalized = _normalize_codex_event(event)
        if normalized is None:
            continue
        if normalized["event_type"] == "tool_started":
            tool_call_count += 1
        if on_event is not None:
            on_event(normalized)
    return event_count, tool_call_count


def _json_object(line: str) -> dict[str, object] | None:
    """把单行 JSON 收窄为对象，非法文本和数组等其他 JSON 值返回空。"""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _normalize_codex_event(event: dict[str, object]) -> AgentTraceEvent | None:
    """把 Codex 版本相关 JSON 对象转换为稳定且最小的外部动作事件。"""
    source_type = str(event.get("type", ""))
    if source_type == "thread.started":
        payload: dict[str, object] = {"source_type": source_type}
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str):
            payload["thread_id"] = thread_id
        return {
            "event_type": "agent_session_started",
            "actor": "codex",
            "message": "Codex 会话已启动",
            "payload": payload,
        }
    if source_type == "turn.started":
        return {
            "event_type": "agent_turn_started",
            "actor": "codex",
            "message": "Codex 开始处理任务",
            "payload": {"source_type": source_type},
        }

    # item 事件必须同时包含对象载荷和受支持类型，未知未来字段不会泄漏进审计日志。
    item = event.get("item")
    if source_type not in {"item.started", "item.completed"} or not isinstance(item, dict):
        return None
    item_type = str(item.get("type", ""))
    if source_type == "item.completed" and item_type == "agent_message":
        text = _truncated_text(item.get("text"), 1_000)
        return {
            "event_type": "agent_message",
            "actor": "codex",
            "message": text,
            "payload": {"text": text},
        }

    if item_type not in {"command_execution", "mcp_tool_call", "file_change"}:
        return None
    tool_name = _tool_name(item_type, item)
    command = _truncated_text(item.get("command"), 1_000)
    payload = {"tool_name": tool_name, "command": command}
    if source_type == "item.started":
        return {
            "event_type": "tool_started",
            "actor": "codex",
            "message": command or tool_name,
            "payload": payload,
        }

    # 完成事件只加入退出码和安全截断输出，不复制 MCP 参数或其他未知动态字段。
    exit_code = item.get("exit_code")
    if isinstance(exit_code, int):
        payload["exit_code"] = exit_code
    output = _truncated_text(item.get("aggregated_output", item.get("output")), 2_000)
    payload["output"] = output
    return {
        "event_type": "tool_finished",
        "actor": "codex",
        "message": command or tool_name,
        "payload": payload,
    }


def _tool_name(item_type: str, item: dict[str, object]) -> str:
    """为命令、MCP 和文件动作生成不包含动态参数的稳定工具名称。"""
    if item_type == "command_execution":
        return "command"
    if item_type == "file_change":
        return "file_change"
    candidate = item.get("tool") or item.get("name")
    return str(candidate) if isinstance(candidate, str) else "mcp_tool"


def _truncated_text(value: object, limit: int) -> str:
    """把受支持的文本值截断到持久化上限，其他动态结构返回空字符串。"""
    if not isinstance(value, str):
        return ""
    return value if len(value) <= limit else value[:limit]


def _error_detail(stderr: str, *, limit: int = 2_000) -> str:
    """截断 CLI 错误文本，既保留诊断信息又限制任务结果体积。"""
    detail = stderr.strip() or "no stderr"
    if len(detail) <= limit:
        return detail
    return detail[-limit:]
