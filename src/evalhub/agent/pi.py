"""运行固定 Pi CLI Agent 壳并提取可评分结果。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from http import client as http_client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import IO, Protocol
from urllib.parse import urlparse

from evalhub.agent.base import (
    AgentRunError,
    AgentRunResult,
    AgentTraceEvent,
    TraceCallback,
)
from evalhub.ollama_pull import validate_loopback_base_url

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PI_BINARY = _PROJECT_ROOT / "agent-runtime" / "node_modules" / ".bin" / "pi"
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_API_PROVIDERS = {
    "deepseek": ("https://api.deepseek.com", "api.deepseek.com", ""),
    "siliconflow": ("https://api.siliconflow.cn/v1", "api.siliconflow.cn", "/v1"),
}


PiAgentError = AgentRunError
PiRunResult = AgentRunResult


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
        adapter: str = "ollama",
        provider_id: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """注入版本探测、流式进程边界和模型服务凭据。

        参数：
            run_command: 用于轻量 CLI 版本探测的同步执行器。
            process_factory: 用于真实 Agent 运行的流式进程构造器；生产默认 ``Popen``。
            adapter: 本地 ``ollama`` 或当前支持的 ``openai-compatible``。
            provider_id: API 模式支持的固定服务商 ID；本地模式留空。
            api_key: 仅在当前进程内交给受控代理的 API 凭据，不写入配置文件。
        """
        self._run_command = run_command
        self._process_factory = process_factory or subprocess.Popen
        self._adapter = adapter
        self._provider_id = provider_id
        self._api_key = api_key
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
            model: Ollama 标签或受支持 API 服务商的公开模型 ID。
            base_url: 本机 Ollama 地址或固定服务商的官方 API 地址。
            workspace: Agent 唯一可写的样本工作区。
            timeout_seconds: 本次样本允许的最长执行秒数。
            on_event: 每产生一条白名单外部事件时立即调用的可选回调。

        返回：
            包含最终消息、事件数量、耗时和 CLI 版本的执行结果。

        异常：
            PiAgentError: 参数无效、CLI 不可用、超时、退出失败或结果缺失时抛出。
        """
        _validate_run_arguments(instruction, model, base_url, workspace, timeout_seconds)
        normalized_base_url, provider = self._provider_settings(base_url)

        # Pi 会切换子进程 cwd；提前绝对化可避免沙箱根和配置路径被二次相对解析。
        workspace = workspace.resolve()
        cli_version = self.version()

        # 每个样本把 Pi 状态和临时文件限制在自己的工作区，避免污染用户配置。
        evalhub_dir = workspace / ".evalhub"
        pi_home = evalhub_dir / "pi-home"
        temp_dir = evalhub_dir / "tmp"
        pi_home.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        proxy: tuple[ThreadingHTTPServer, Thread] | None = None
        try:
            runtime_base_url = normalized_base_url
            if provider == "ollama":
                _write_models_config(pi_home, runtime_base_url, model)
            else:
                # 父进程代理独占真实凭据，Pi 及其 bash 工具只得到无权外联的本机地址。
                server, thread, runtime_base_url = _start_api_proxy(str(self._api_key), provider)
                proxy = (server, thread)
                _write_api_proxy_config(pi_home, runtime_base_url, model, provider)

            # 命令和环境由平台固定，沙箱网络只允许连接当前本机模型端点。
            command = _build_command(
                instruction=instruction,
                model=model,
                workspace=workspace,
                base_url=runtime_base_url,
                provider=provider,
            )
            environment = _pi_environment(pi_home, temp_dir)

            # 生产路径逐行读取 JSONL；旧测试注入方式仍复用同一标准化逻辑。
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
                raise PiAgentError(
                    "Pi CLI or macOS sandbox-exec is unavailable",
                    error_type="executor_not_ready",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                timeout_label = f"{timeout_seconds:g}"
                raise PiAgentError(f"pi timed out after {timeout_label} seconds") from exc
            elapsed_seconds = monotonic() - started_at
        finally:
            if proxy is not None:
                _stop_api_proxy(*proxy)

        # 只有退出成功且 JSONL 含权威 assistant message_end 才算可审计执行。
        if return_code != 0:
            detail = _error_detail(stderr)
            error_type = (
                "executor_not_ready"
                if return_code == 71 and "sandbox-exec" in detail
                else None
            )
            raise PiAgentError(
                f"pi exited with code {return_code}: {detail}",
                error_type=error_type,
            )
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

    def _provider_settings(self, base_url: str) -> tuple[str, str]:
        """校验当前模型来源并返回规范地址与 Pi Provider 名。

        参数：
            base_url: 任务创建时冻结的模型服务根地址。

        返回：
            规范化服务地址和 Pi 内置 Provider 名。

        异常：
            PiAgentError: 适配器、服务商、地址或 API Key 不符合固定安全边界。
        """
        if self._adapter == "ollama":
            try:
                return validate_loopback_base_url(base_url), "ollama"
            except ValueError as exc:
                raise PiAgentError(str(exc)) from exc

        # API Agent 只开放经过端点约束的官方服务，避免自定义 Provider 扩大网络面。
        normalized_base_url = base_url.strip().rstrip("/")
        if self._adapter != "openai-compatible" or self._provider_id not in _API_PROVIDERS:
            supported = ", ".join(_API_PROVIDERS)
            raise PiAgentError(f"Pi API agent provider must be one of: {supported}")
        provider = str(self._provider_id)
        expected_base_url = _API_PROVIDERS[provider][0]
        if normalized_base_url != expected_base_url:
            raise PiAgentError(f"{provider} agent base_url must be {expected_base_url}")
        if not self._api_key or not self._api_key.strip():
            raise PiAgentError(f"{provider} API Key is required")
        return normalized_base_url, provider

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


def _write_api_proxy_config(pi_home: Path, base_url: str, model: str, provider: str) -> None:
    """把远程 API 模型改道到单次运行的本机凭据代理。

    参数：
        pi_home: 当前样本专用的 Pi 配置目录。
        base_url: 随机端口本机代理地址。
        model: 本次选择的远程模型 ID。
        provider: 已验证的 DeepSeek 或 SiliconFlow 服务商 ID。

    异常：
        OSError: 配置目录不可写时保留原始文件系统诊断。
    """
    if provider == "deepseek":
        provider_config: dict[str, object] = {
            "baseUrl": base_url,
            "apiKey": "evalhub-proxy",
            "modelOverrides": {
                model: {
                    "compat": {"supportsDeveloperRole": False},
                }
            },
        }
    else:
        # SiliconFlow 没有 Pi 内置模型目录，仅声明本次模型需要的 OpenAI 兼容字段。
        provider_config = {
            "baseUrl": base_url,
            "api": "openai-completions",
            "apiKey": "evalhub-proxy",
            "compat": {
                "supportsDeveloperRole": False,
                "supportsReasoningEffort": False,
            },
            "models": [{"id": model}],
        }
    config = {"providers": {provider: provider_config}}
    # 配置只含本机代理的无权占位 Key，真实凭据不会进入 Agent 可读目录。
    (pi_home / "models.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pi_environment(pi_home: Path, temp_dir: Path) -> dict[str, str]:
    """构造不含远程模型凭据的隔离 Pi 子进程环境。

    参数：
        pi_home: 当前样本专用的 Pi 配置目录。
        temp_dir: 当前样本专用的临时文件目录。

    返回：
        保留系统运行变量但关闭更新、遥测和用户全局状态的环境字典。
    """
    allowed_names = {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SHELL",
        "TERM",
        "USER",
        "LOGNAME",
    }
    environment = {name: value for name, value in os.environ.items() if name in allowed_names}
    # HOME 指向样本隔离目录，避免 Pi 或 bash 工具按默认路径读取用户级配置和凭据。
    environment.update(
        {
            "HOME": str(pi_home),
            "PI_CODING_AGENT_DIR": str(pi_home),
            "PI_CODING_AGENT_SESSION_DIR": str(pi_home / "sessions"),
            "PI_OFFLINE": "1",
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY": "0",
            "TMPDIR": str(temp_dir),
        }
    )
    return environment


def _start_api_proxy(api_key: str, provider: str) -> tuple[ThreadingHTTPServer, Thread, str]:
    """启动只转发固定官方对话端点并注入凭据的本机代理。

    参数：
        api_key: 仅由父 Worker 持有的真实 API Key。
        provider: 已通过白名单校验的服务商 ID。

    返回：
        HTTP 服务器、服务线程和可写入 Pi 配置的本机根地址。

    异常：
        OSError: 本机无法分配监听端口时抛出。
    """
    handler = _api_proxy_handler(api_key, provider)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, name=f"evalhub-{provider}-proxy", daemon=True)
    thread.start()
    port = int(server.server_address[1])
    return server, thread, f"http://localhost:{port}"


def _stop_api_proxy(server: ThreadingHTTPServer, thread: Thread) -> None:
    """停止单次样本的本机 API 代理并回收监听端口。

    参数：
        server: 当前样本创建的 HTTP 服务器。
        thread: 执行 ``serve_forever`` 的后台线程。
    """
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)


def _api_proxy_handler(api_key: str, provider: str) -> type[BaseHTTPRequestHandler]:
    """创建捕获单个凭据且不记录请求内容的固定上游代理处理器。

    参数：
        api_key: 转发到官方服务时注入的认证凭据。
        provider: 已通过白名单校验的服务商 ID。

    返回：
        可交给 ``ThreadingHTTPServer`` 的请求处理器类型。
    """

    _, upstream_host, path_prefix = _API_PROVIDERS[provider]

    class ApiProxyHandler(BaseHTTPRequestHandler):
        """仅转发 Pi 所需的 Chat Completions POST 请求。"""

        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            """校验固定路径和正文上限，再流式转发到固定官方 HTTPS。"""
            if self.path not in {"/chat/completions", "/v1/chat/completions"}:
                self.send_error(404, "unsupported API proxy path")
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return

            # Coding Mini 请求远小于此上限；显式限制可避免 Agent 滥用本机代理内存。
            if content_length <= 0 or content_length > 10 * 1024 * 1024:
                self.send_error(413, "request body is empty or too large")
                return
            body = self.rfile.read(content_length)
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": self.headers.get("Content-Type", "application/json"),
                "Accept": self.headers.get("Accept", "application/json"),
            }

            # 上游连接只认固定官方主机，Agent 无法借请求路径选择任意目标。
            connection = http_client.HTTPSConnection(upstream_host, 443, timeout=200)
            try:
                upstream_path = self.path
                if path_prefix and not upstream_path.startswith(f"{path_prefix}/"):
                    upstream_path = f"{path_prefix}{upstream_path}"
                connection.request("POST", upstream_path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in {"content-length", "transfer-encoding", "connection"}:
                        self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()

                # ``http.client`` 已解码上游 chunked framing；下游以关闭连接作为正文边界。
                while chunk := response.read(64 * 1024):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                self.close_connection = True
            except BrokenPipeError:
                # Pi 超时后会先关闭本机连接；响应头已发送时不能再尝试回写一份 502。
                self.close_connection = True
            except (OSError, http_client.HTTPException):
                self.send_error(502, "API upstream request failed")
            finally:
                connection.close()

        def log_message(self, format: str, *args: object) -> None:
            """关闭默认访问日志，避免模型请求元数据进入任务外部输出。"""
            del format, args

    return ApiProxyHandler


def _build_command(
    *, instruction: str, model: str, workspace: Path, base_url: str, provider: str
) -> list[str]:
    """构造固定且可审计的 Pi 沙箱命令。

    参数：
        instruction: 交给 Pi 的单个编码任务。
        model: Ollama 模型标签或受支持 API 服务商的公开模型 ID。
        workspace: 已绝对化的唯一可写样本目录。
        base_url: 已验证的 Ollama 或本机 API 代理根地址。
        provider: 已收窄为 ``ollama`` 或受支持 API 服务商的 Pi Provider 名。

    返回：
        可直接交给 subprocess 且不经 shell 解释的参数列表。
    """
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    remote = f"localhost:{port}"
    sandbox_profile = _sandbox_profile(remote)
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
        provider,
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


def _sandbox_profile(remote: str) -> str:
    """生成仅允许工作区写入与单个模型端点连接的 Seatbelt 策略。

    参数：
        remote: 由内部固定逻辑生成的 ``主机:端口`` 网络目标。

    返回：
        由 ``sandbox-exec`` 读取的完整策略文本；工作区通过参数传入，避免路径注入。
    """
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        '(allow file-write* (subpath (param "WORKSPACE")))\n'
        "(deny network*)\n"
        f'(allow network-outbound (remote tcp "{remote}"))\n'
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
