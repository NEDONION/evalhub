"""通过 MiniClaw 自身 Python 环境运行完整 Agent 并解析安全 JSONL。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import IO, Protocol
from uuid import uuid4

from evalhub.agent.base import (
    AgentMetadata,
    AgentRunError,
    AgentRunResult,
    AgentTraceEvent,
    TraceCallback,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BRIDGE_SCRIPT = Path(__file__).with_name("miniclaw_bridge.py")
_TERMINATION_GRACE_SECONDS = 2.0
_SAFE_BRIDGE_ERRORS = {
    "agent_error": "MiniClaw agent run failed",
    "approval_required": "MiniClaw requires interactive approval",
    "executor_not_ready": "MiniClaw runtime is not ready",
    "provider_error": "MiniClaw provider request failed",
}


class CompletedCommand(Protocol):
    """描述 metadata 探测需要读取的同步命令结果。"""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """描述可注入的同步 describe 命令执行边界。"""

    def __call__(self, command: list[str], **kwargs: object) -> CompletedCommand:
        """执行命令并返回 stdout、stderr 和退出码。"""


class StreamingProcess(Protocol):
    """描述 MiniClaw JSONL 子进程所需的最小生命周期接口。"""

    stdin: IO[str] | None
    stdout: IO[str] | None
    stderr: IO[str] | None
    returncode: int | None
    pid: int

    def poll(self) -> int | None:
        """返回当前退出码；进程运行中返回空值。"""

    def wait(self, timeout: float | None = None) -> int:
        """等待进程结束并返回退出码。"""

    def terminate(self) -> None:
        """请求进程优雅终止。"""

    def kill(self) -> None:
        """强制终止仍未退出的进程。"""


class ProcessFactory(Protocol):
    """描述可替换的 MiniClaw 流式进程构造器。"""

    def __call__(self, command: list[str], **kwargs: object) -> StreamingProcess:
        """启动固定命令并返回可读取的子进程。"""


def resolve_miniclaw_root(environ: Mapping[str, str] | None = None) -> Path:
    """按显式环境变量和仓库同级目录解析 MiniClaw 项目根。

    Args:
        environ: 可替换的环境变量映射；默认读取当前进程环境。

    Returns:
        规范化后的绝对 MiniClaw 项目路径。

    Raises:
        ValueError: 显式路径不是绝对路径。
    """
    source = os.environ if environ is None else environ
    explicit = source.get("EVALHUB_MINICLAW_ROOT")
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            raise ValueError("EVALHUB_MINICLAW_ROOT must be an absolute path")
        return candidate.resolve(strict=False)

    # 手工 worktree 位于主仓库的 .worktrees 下，默认仍应定位两个主项目的共同父目录。
    project_parent = _PROJECT_ROOT.parent
    if project_parent.name == ".worktrees":
        project_parent = _PROJECT_ROOT.parents[2]
    return (project_parent / "miniclaw").resolve(strict=False)


class MiniClawAgentRunner:
    """启动 MiniClaw 自身虚拟环境并转换其无头桥事件。"""

    def __init__(
        self,
        *,
        root: Path | None = None,
        run_command: CommandRunner = subprocess.run,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        """绑定项目路径和可替换的子进程边界。

        Args:
            root: MiniClaw 项目根；省略时按安全规则自动发现。
            run_command: 只用于轻量 describe 探测的同步执行器。
            process_factory: 用于正式 JSONL 运行的进程构造器。
        """
        self._root = (root or resolve_miniclaw_root()).resolve(strict=False)
        self._python = self._root / ".venv" / "bin" / "python"
        self._run_command = run_command
        self._process_factory = process_factory
        self._metadata: AgentMetadata | None = None

    def metadata(self) -> AgentMetadata:
        """通过无头桥读取 MiniClaw 版本、自管模型和运行指纹。

        Returns:
            不含 Base URL、文件正文或凭据的完整 Agent 元数据。

        Raises:
            AgentRunError: 项目、配置、Home 或凭据未准备好。
        """
        if self._metadata is not None:
            return self._metadata
        self._validate_runtime_paths()

        # describe 只读取元数据，限制十秒避免损坏的外部环境挂住任务 API。
        try:
            completed = self._run_command(
                [str(self._python), str(_BRIDGE_SCRIPT), "describe"],
                cwd=self._root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentRunError(
                "MiniClaw metadata check failed",
                error_type="executor_not_ready",
            ) from exc

        payload = _describe_payload(completed)
        if payload.get("available") is not True:
            message = _safe_status_message(payload.get("message"))
            raise AgentRunError(message, error_type="executor_not_ready")

        version = _required_text(payload, "version", "MiniClaw version is unavailable")
        model = _required_text(payload, "model", "MiniClaw model is unavailable")
        fingerprint = _optional_text(payload.get("runtime_fingerprint"))
        self._metadata = AgentMetadata("miniclaw", "MiniClaw", version, model, fingerprint)
        return self._metadata

    def run(
        self,
        *,
        instruction: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> AgentRunResult:
        """在独立 MiniClaw 子进程运行一条 Coding Mini 任务。

        Args:
            instruction: 只通过 stdin 发送的公开任务说明。
            workspace: 当前样本唯一工作区。
            timeout_seconds: 包含模型和工具执行的总时限。
            on_event: 每条白名单事件产生后立即调用的回调。

        Returns:
            最终文本、工具次数、版本和真实耗时。

        Raises:
            AgentRunError: 输入、外部环境、协议、超时或 Agent 执行失败。
        """
        _validate_run_arguments(instruction, workspace, timeout_seconds)
        self._validate_runtime_paths()
        command = self._run_command_line(workspace)

        try:
            process = self._process_factory(
                command,
                cwd=self._root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise AgentRunError(
                "MiniClaw process could not start",
                error_type="executor_not_ready",
            ) from exc

        started_at = monotonic()
        try:
            _write_instruction(process, instruction)
            terminal, event_count = _read_jsonl(
                process,
                timeout_seconds=timeout_seconds,
                on_event=on_event,
            )
        except AgentRunError:
            _terminate_process(process)
            raise
        elapsed = monotonic() - started_at

        return_code = _wait_for_exit(process, max(0.0, timeout_seconds - elapsed))
        if terminal is None:
            if return_code != 0:
                raise AgentRunError(f"MiniClaw bridge exited with code {return_code}")
            raise AgentRunError("MiniClaw bridge produced no terminal result")
        if terminal["type"] == "error":
            raise _terminal_error(terminal)
        if return_code != 0:
            raise AgentRunError(f"MiniClaw bridge exited with code {return_code}")

        return AgentRunResult(
            final_message=str(terminal["final_message"]),
            event_count=event_count,
            return_code=return_code,
            wall_time_seconds=elapsed,
            cli_version=str(terminal["version"]),
            tool_call_count=int(terminal["tool_call_count"]),
        )

    def _validate_runtime_paths(self) -> None:
        """拒绝缺失的外部项目、解释器或 EvalHub 桥脚本。

        Raises:
            AgentRunError: 任一固定运行路径不可用。
        """
        if not self._root.is_dir():
            raise AgentRunError("MiniClaw project is missing", error_type="executor_not_ready")
        if not self._python.is_file() or not os.access(self._python, os.X_OK):
            raise AgentRunError(
                "MiniClaw Python environment is missing",
                error_type="executor_not_ready",
            )
        if not _BRIDGE_SCRIPT.is_file():
            raise AgentRunError(
                "EvalHub MiniClaw bridge is missing",
                error_type="executor_not_ready",
            )

    def _run_command_line(self, workspace: Path) -> list[str]:
        """构造不含任务说明和凭据的固定子进程参数。

        Args:
            workspace: 用于派生稳定会话标识的样本工作区。

        Returns:
            可以直接交给 ``Popen`` 且不经过 shell 的参数列表。
        """
        conversation_id = f"evalhub-{workspace.parent.name}-{workspace.name}-{uuid4().hex[:8]}"
        return [
            str(self._python),
            str(_BRIDGE_SCRIPT),
            "run",
            "--workspace",
            str(workspace.resolve()),
            "--conversation-id",
            conversation_id,
        ]


def _describe_payload(completed: CompletedCommand) -> dict[str, object]:
    """把 describe 命令收窄为单个 JSON 对象。

    Args:
        completed: 外部 Python 已完成的命令结果。

    Returns:
        通过基本结构校验的描述对象。

    Raises:
        AgentRunError: 命令失败、输出缺失或不是 JSON 对象。
    """
    if completed.returncode != 0:
        raise AgentRunError("MiniClaw metadata check failed", error_type="executor_not_ready")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AgentRunError(
            "MiniClaw metadata is invalid",
            error_type="executor_not_ready",
        ) from exc
    if not isinstance(payload, dict):
        raise AgentRunError("MiniClaw metadata is invalid", error_type="executor_not_ready")
    return payload


def _safe_status_message(value: object) -> str:
    """只接受桥生成的短状态消息，拒绝任意长动态响应。"""
    if not isinstance(value, str) or not value.strip():
        return "MiniClaw runtime is not ready"
    return value.strip()[:300]


def _required_text(payload: dict[str, object], key: str, error: str) -> str:
    """读取非空字符串字段，并把协议缺失转换为安全错误。

    Args:
        payload: 已确认是对象的 describe 结果。
        key: 必须存在的字段名。
        error: 缺失时使用的稳定消息。

    Returns:
        去除首尾空白的字段值。

    Raises:
        AgentRunError: 字段不是非空字符串。
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentRunError(error, error_type="executor_not_ready")
    return value.strip()


def _optional_text(value: object) -> str | None:
    """把可选动态值收窄为非空短字符串。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:300]


def _validate_run_arguments(instruction: str, workspace: Path, timeout_seconds: float) -> None:
    """在创建外部进程前验证公开任务、工作区和超时。

    Raises:
        AgentRunError: 任务为空、工作区无效或超时不为正数。
    """
    if not instruction.strip():
        raise AgentRunError("MiniClaw instruction must not be empty")
    if not workspace.is_dir():
        raise AgentRunError("MiniClaw workspace is not a directory")
    if timeout_seconds <= 0:
        raise AgentRunError("MiniClaw timeout must be positive")


def _write_instruction(process: StreamingProcess, instruction: str) -> None:
    """把公开任务作为单个 JSON 对象写入子进程 stdin。

    Raises:
        AgentRunError: stdin 不可用或写入失败。
    """
    if process.stdin is None:
        raise AgentRunError("MiniClaw bridge stdin is unavailable")
    try:
        process.stdin.write(json.dumps({"instruction": instruction}, ensure_ascii=False))
        process.stdin.write("\n")
        process.stdin.flush()
        process.stdin.close()
    except OSError as exc:
        raise AgentRunError("MiniClaw bridge stdin failed") from exc


def _read_jsonl(
    process: StreamingProcess,
    *,
    timeout_seconds: float,
    on_event: TraceCallback | None,
) -> tuple[dict[str, object] | None, int]:
    """按时限读取 JSONL，并把有效事件实时交给调用方。

    Args:
        process: 已启动且 stdout 可读的 MiniClaw 子进程。
        timeout_seconds: 本次完整读取允许的最长秒数。
        on_event: 接收白名单事件的可选回调。

    Returns:
        唯一终态对象和已经转发的事件数。

    Raises:
        AgentRunError: 输出缺失、JSON 无效、终态重复或读取超时。
    """
    if process.stdout is None:
        raise AgentRunError("MiniClaw bridge stdout is unavailable")
    output: Queue[str | None] = Queue()
    reader = Thread(target=_read_lines, args=(process.stdout, output), daemon=True)
    reader.start()
    if process.stderr is not None:
        Thread(target=_drain_stream, args=(process.stderr,), daemon=True).start()

    terminal: dict[str, object] | None = None
    event_count = 0
    deadline = monotonic() + timeout_seconds
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AgentRunError(f"MiniClaw timed out after {timeout_seconds:g} seconds")
        try:
            line = output.get(timeout=min(0.1, remaining))
        except Empty:
            continue
        if line is None:
            break

        item = _parse_output_line(line)
        if item["type"] == "event":
            event = _event_payload(item)
            if event is not None:
                event_count += 1
                if on_event is not None:
                    on_event(event)
            continue
        if terminal is not None:
            raise AgentRunError("MiniClaw bridge produced multiple terminal results")
        terminal = item
    return terminal, event_count


def _read_lines(stream: IO[str], output: Queue[str | None]) -> None:
    """在守护线程中逐行读取 stdout，并用空值标记 EOF。"""
    try:
        for line in stream:
            output.put(line)
    finally:
        output.put(None)


def _drain_stream(stream: IO[str]) -> None:
    """持续排空 stderr，避免外部进程因管道填满而阻塞。"""
    for _ in stream:
        pass


def _parse_output_line(line: str) -> dict[str, object]:
    """把一行 JSON 收窄为桥协议允许的三类对象。

    Raises:
        AgentRunError: 行不是对象或缺少受支持的类型。
    """
    try:
        item = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AgentRunError("invalid MiniClaw bridge output") from exc
    if not isinstance(item, dict) or item.get("type") not in {"event", "result", "error"}:
        raise AgentRunError("invalid MiniClaw bridge output")
    if item["type"] == "result":
        _validate_result(item)
    if item["type"] == "error" and not isinstance(item.get("code"), str):
        raise AgentRunError("invalid MiniClaw bridge output")
    return item


def _validate_result(item: dict[str, object]) -> None:
    """验证结果终态包含构造通用运行结果所需的字段。

    Raises:
        AgentRunError: 任一字段类型不符合协议。
    """
    if not isinstance(item.get("final_message"), str):
        raise AgentRunError("invalid MiniClaw bridge output")
    if type(item.get("tool_call_count")) is not int:
        raise AgentRunError("invalid MiniClaw bridge output")
    if not isinstance(item.get("version"), str) or not isinstance(item.get("model"), str):
        raise AgentRunError("invalid MiniClaw bridge output")


def _event_payload(item: dict[str, object]) -> AgentTraceEvent | None:
    """读取已由桥白名单化的事件，结构不完整时安全忽略。"""
    event = item.get("event")
    if not isinstance(event, dict):
        return None
    event_type = event.get("event_type")
    actor = event.get("actor")
    payload = event.get("payload")
    message = event.get("message")
    if not isinstance(event_type, str) or not isinstance(actor, str):
        return None
    if not isinstance(payload, dict) or (message is not None and not isinstance(message, str)):
        return None
    return {
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "payload": payload,
    }


def _wait_for_exit(process: StreamingProcess, timeout_seconds: float) -> int:
    """等待 stdout 结束后的真实进程终态，超时则回收进程组。

    Raises:
        AgentRunError: 进程在剩余时间内没有退出。
    """
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise AgentRunError("MiniClaw process did not exit after output ended") from exc


def _terminal_error(terminal: dict[str, object]) -> AgentRunError:
    """把不可信桥错误正文转换为固定安全消息和基础设施分类。"""
    code = str(terminal.get("code", "agent_error"))
    message = _SAFE_BRIDGE_ERRORS.get(code, "MiniClaw agent run failed")
    error_type = "executor_not_ready" if code == "executor_not_ready" else None
    return AgentRunError(message, error_type=error_type)


def _terminate_process(process: StreamingProcess) -> None:
    """先终止独立进程组，再强制回收没有及时退出的后代进程。"""
    if process.poll() is not None:
        return
    _signal_process_group(process, signal.SIGTERM, process.terminate)
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass

    _signal_process_group(process, signal.SIGKILL, process.kill)
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()


def _signal_process_group(
    process: StreamingProcess,
    signum: signal.Signals,
    fallback: Callable[[], None],
) -> None:
    """向独立进程组发送信号，不支持时退回单进程生命周期方法。"""
    try:
        os.killpg(process.pid, signum)
    except (OSError, AttributeError):
        fallback()
