"""运行固定 Pi CLI Agent 壳并提取可评分结果。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import IO, Protocol, TypedDict
from urllib.parse import urlparse

from evalhub.ollama_pull import validate_loopback_base_url

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PI_BINARY = _PROJECT_ROOT / "agent-runtime" / "node_modules" / ".bin" / "pi"
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


class PiAgentError(RuntimeError):
    """表示 Pi CLI 未能产生可评分的 Agent 执行结果。"""


@dataclass(frozen=True)
class PiRunResult:
    """记录一次固定 Agent 壳运行后需要持久化的审计信息。"""

    final_message: str
    event_count: int
    return_code: int
    wall_time_seconds: float
    cli_version: str
    tool_call_count: int = 0


class AgentTraceEvent(TypedDict):
    """描述可跨进程持久化的单条 Pi 外部可观察事件。"""

    event_type: str
    actor: str
    message: str | None
    payload: dict[str, object]


TraceCallback = Callable[[AgentTraceEvent], None]


class CommandRunner(Protocol):
    """描述可替换的子进程执行函数，便于测试时隔离真实 Pi。"""

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """执行命令并返回已完成进程；关键字参数遵循 ``subprocess.run``。"""


class StreamingProcess(Protocol):
    """描述流式 Pi 子进程需要暴露的最小生命周期接口。"""

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
    """描述可注入的 Pi 流式进程构造器。"""

    def __call__(self, command: list[str], **kwargs: object) -> StreamingProcess:
        """启动命令并返回可读取 stdout/stderr 的进程。"""


class PiAgentRunner:
    """通过受约束的 Pi CLI 命令运行本地 Ollama 模型。"""

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
        """读取并缓存 Pi CLI 版本。

        返回：
            CLI 输出的单行版本文本。

        异常：
            PiAgentError: CLI 不存在、退出失败或没有返回版本时抛出。
        """
        if self._cli_version is not None:
            return self._cli_version

        # 版本探测沿用同一个可替换执行器，使单元测试不依赖用户机器环境。
        try:
            completed = self._run_command(
                [str(_PI_BINARY), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PiAgentError(
                "project Pi CLI is missing; run npm --prefix agent-runtime ci --ignore-scripts"
            ) from exc

        # Pi 0.74.1 在非 TTY 下把版本写入 stderr；只有非零退出才把该流当错误详情。
        if completed.returncode != 0:
            detail = _error_detail(completed.stderr)
            raise PiAgentError(f"pi version check failed: {detail}")
        version = completed.stdout.strip() or completed.stderr.strip()
        if not version:
            raise PiAgentError("pi version check produced no output")
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
    ) -> PiRunResult:
        """在指定工作区运行固定 Pi Agent 壳。

        参数：
            instruction: 交给 Agent 的单个编码任务说明。
            model: 通过 Ollama 暴露的基模名称。
            base_url: 仅允许本机回环地址的 Ollama 服务根地址。
            workspace: Agent 唯一可写的样本工作区。
            timeout_seconds: 本次样本允许的最长执行秒数。
            on_event: 每产生一条白名单外部事件时立即调用的可选回调。

        返回：
            包含最终消息、事件数量、耗时和 CLI 版本的执行结果。

        异常：
            PiAgentError: 参数无效、CLI 不可用、超时、退出失败或结果缺失时抛出。
        """
        _validate_run_arguments(instruction, model, base_url, workspace, timeout_seconds)
        try:
            normalized_base_url = validate_loopback_base_url(base_url)
        except ValueError as exc:
            raise PiAgentError(str(exc)) from exc

        # Pi 会切换子进程 cwd；提前绝对化可避免沙箱根和配置路径被二次相对解析。
        workspace = workspace.resolve()
        cli_version = self.version()

        # 每个样本把 Pi 状态和临时文件限制在自己的工作区，避免污染用户配置。
        evalhub_dir = workspace / ".evalhub"
        pi_home = evalhub_dir / "pi-home"
        temp_dir = evalhub_dir / "tmp"
        pi_home.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        _write_models_config(pi_home, normalized_base_url, model)

        # 命令参数与 Seatbelt 策略由平台固定，用户只能选择基模和本机 Ollama 地址。
        command = _build_command(
            instruction=instruction,
            model=model,
            workspace=workspace,
            base_url=normalized_base_url,
        )
        environment = os.environ.copy()
        environment.update(
            {
                "PI_CODING_AGENT_DIR": str(pi_home),
                "PI_CODING_AGENT_SESSION_DIR": str(pi_home / "sessions"),
                "PI_OFFLINE": "1",
                "PI_SKIP_VERSION_CHECK": "1",
                "PI_TELEMETRY": "0",
                "TMPDIR": str(temp_dir),
            }
        )

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
            raise PiAgentError("Pi CLI or macOS sandbox-exec is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            timeout_label = f"{timeout_seconds:g}"
            raise PiAgentError(f"pi timed out after {timeout_label} seconds") from exc
        elapsed_seconds = monotonic() - started_at

        # 只有退出成功且 JSONL 含权威 assistant message_end 才算可审计执行。
        if return_code != 0:
            detail = _error_detail(stderr)
            raise PiAgentError(f"pi exited with code {return_code}: {detail}")
        final_message = _final_message(stdout)
        if not final_message:
            raise PiAgentError("pi produced no final message")

        # 流式路径已经实时发送事件；这里仅从完整 stdout 重新计算可审计计数。
        event_count, tool_call_count = _emit_stdout_events(stdout, None)

        return PiRunResult(
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
        """执行 Pi 并返回退出码及完整的安全边界输出。

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
            start_new_session=True,
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
        raise PiAgentError("instruction must not be empty")
    if not model.strip():
        raise PiAgentError("model must not be empty")
    if not base_url.strip():
        raise PiAgentError("base_url must not be empty")

    # 工作区必须由 benchmark 提前创建，Runner 不猜测或扩大可写范围。
    if not workspace.is_dir():
        raise PiAgentError(f"workspace does not exist: {workspace}")
    if timeout_seconds <= 0:
        raise PiAgentError("timeout_seconds must be greater than zero")


def _write_models_config(pi_home: Path, base_url: str, model: str) -> None:
    """写入只含当前 Ollama 模型的隔离 Pi 配置。

    参数：
        pi_home: 当前样本专用的 Pi 配置目录。
        base_url: 已通过回环地址校验且移除末尾斜杠的 Ollama 根地址。
        model: 本次评测选择的 Ollama 模型标签。

    异常：
        OSError: 配置目录不可写时保留原始文件系统诊断。
    """
    config = {
        "providers": {
            "ollama": {
                "baseUrl": f"{base_url}/v1",
                "api": "openai-completions",
                "apiKey": "ollama",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": model}],
            }
        }
    }
    # 使用 JSON 编码而不是字符串拼接，模型标签无法逃逸配置结构。
    (pi_home / "models.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_command(
    *, instruction: str, model: str, workspace: Path, base_url: str
) -> list[str]:
    """构造固定且可审计的 Pi 本地 Ollama 沙箱命令。

    参数：
        instruction: 交给 Pi 的单个编码任务。
        model: 在隔离配置中注册的 Ollama 模型标签。
        workspace: 已绝对化的唯一可写样本目录。
        base_url: 已验证的 HTTP 回环 Ollama 根地址。

    返回：
        可直接交给 subprocess 且不经 shell 解释的参数列表。
    """
    parsed = urlparse(base_url)
    port = parsed.port or 80
    sandbox_profile = _sandbox_profile(port)
    return [
        str(_SANDBOX_EXEC),
        "-p",
        sandbox_profile,
        f"-DWORKSPACE={workspace}",
        "--",
        str(_PI_BINARY),
        "--mode",
        "json",
        "--no-session",
        "--provider",
        "ollama",
        "--model",
        model,
        "--tools",
        "read,write,edit,bash",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        instruction,
    ]


def _sandbox_profile(ollama_port: int) -> str:
    """生成仅允许工作区写入与本机 Ollama 连接的 Seatbelt 策略。

    参数：
        ollama_port: 已从受信回环 URL 解析出的 TCP 端口。

    返回：
        由 ``sandbox-exec`` 读取的完整策略文本；工作区通过参数传入，避免路径注入。
    """
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        '(allow file-write* (subpath (param "WORKSPACE")))\n'
        "(deny network*)\n"
        f'(allow network-outbound (remote tcp "localhost:{ollama_port}"))\n'
    )


def _consume_process(
    process: StreamingProcess,
    *,
    timeout_seconds: float,
    on_event: TraceCallback | None,
) -> tuple[int, str, str]:
    """并发读取子进程输出，在截止时间内实时发送 stdout 白名单事件。

    参数：
        process: 已启动且 stdout/stderr 配置为文本管道的 Pi 进程。
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
            name=f"pi-{source}-reader",
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
                raise subprocess.TimeoutExpired("pi exec", timeout_seconds)
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
        # 回调或读取异常也必须回收 Pi，避免评测 Worker 退出后残留模型客户端。
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
    """先终止独立进程组，再强制回收超过两秒仍未退出的 Pi。

    参数：
        process: 生产环境是通过 ``start_new_session`` 启动的 Pi Popen；测试可用最小 Fake。
    """
    pid = getattr(process, "pid", None)
    if isinstance(pid, int):
        os.killpg(pid, signal.SIGTERM)
    else:
        # 不带 pid 的测试替身沿用最小生命周期接口，生产 Popen 不会进入此分支。
        process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        if isinstance(pid, int):
            os.killpg(pid, signal.SIGKILL)
        else:
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
        normalized = _normalize_pi_event(event)
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


def _normalize_pi_event(event: dict[str, object]) -> AgentTraceEvent | None:
    """把 Pi 版本相关 JSON 对象转换为稳定且最小的外部动作事件。"""
    source_type = str(event.get("type", ""))
    if source_type == "session":
        payload: dict[str, object] = {"source_type": source_type}
        session_id = event.get("id")
        if isinstance(session_id, str):
            payload["session_id"] = session_id
        return {
            "event_type": "agent_session_started",
            "actor": "pi",
            "message": "Pi 会话已启动",
            "payload": payload,
        }
    if source_type == "agent_start":
        return {
            "event_type": "agent_turn_started",
            "actor": "pi",
            "message": "Pi 开始处理任务",
            "payload": {"source_type": source_type},
        }

    # 最终 assistant 消息来自权威 message_end；增量事件不重复持久化。
    if source_type == "message_end":
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return None
        text = _truncated_text(_message_text(message), 1_000)
        return {
            "event_type": "agent_message",
            "actor": "pi",
            "message": text,
            "payload": {"text": text},
        }

    if source_type not in {"tool_execution_start", "tool_execution_end"}:
        return None
    tool_name = _truncated_text(event.get("toolName"), 100) or "tool"
    command = ""
    args = event.get("args")
    if source_type == "tool_execution_start" and isinstance(args, dict):
        candidate = args.get("command") if tool_name == "bash" else args.get("path")
        command = _truncated_text(candidate, 1_000)
    payload = {"tool_name": tool_name, "command": command}
    if source_type == "tool_execution_start":
        return {
            "event_type": "tool_started",
            "actor": "pi",
            "message": command or tool_name,
            "payload": payload,
        }

    # 完成事件只保留文本结果与错误标记，不复制工具输入或未知动态字段。
    output = _truncated_text(_result_text(event.get("result")), 2_000)
    payload["output"] = output
    payload["is_error"] = event.get("isError") is True
    return {
        "event_type": "tool_finished",
        "actor": "pi",
        "message": tool_name,
        "payload": payload,
    }


def _final_message(stdout: str) -> str:
    """返回 Pi JSONL 中最后一条权威 assistant 完成消息。

    参数：
        stdout: Pi JSON 模式产生的完整标准输出。

    返回：
        最后一条非空 assistant ``message_end`` 文本；不存在时返回空字符串。
    """
    final_message = ""
    for line in stdout.splitlines():
        event = _json_object(line)
        if event is None or event.get("type") != "message_end":
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            final_message = _message_text(message).strip() or final_message
    return final_message


def _message_text(message: dict[str, object]) -> str:
    """从 Pi 消息内容块中提取并拼接可展示文本。

    参数：
        message: Pi ``message_end`` 事件中的消息对象。

    返回：
        保持内容块顺序的纯文本；动态或非文本内容返回空字符串。
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    # 图片、thinking 和工具调用块不进入最终自然语言结果。
    texts = [
        str(block["text"])
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "".join(texts)


def _result_text(result: object) -> str:
    """从 Pi 工具结果中提取安全文本，不持久化任意动态结构。

    参数：
        result: ``tool_execution_end`` 的动态结果字段。

    返回：
        工具结果中保持顺序的文本内容；不支持的结构返回空字符串。
    """
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return ""
    return _message_text(result)


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
