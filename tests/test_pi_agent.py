"""验证 Pi Agent Runner 的沙箱命令、JSONL 结果和错误边界。"""

import io
import json
import signal
import subprocess
from pathlib import Path

import pytest

import evalhub.agent.pi as pi_module
from evalhub.agent.pi import PiAgentError, PiAgentRunner


def _pi_stdout(*, final_message: str = "done") -> str:
    """构造包含一次真实工具调用语义的最小 Pi JSONL。

    参数：
        final_message: 权威 assistant message_end 中的文本；空字符串用于缺失结果测试。

    返回：
        可由 Runner 按行解析的 Pi 事件流。
    """
    events: list[dict[str, object]] = [
        {"type": "session", "version": 3, "id": "session-1"},
        {"type": "agent_start"},
        {
            "type": "tool_execution_start",
            "toolCallId": "tool-1",
            "toolName": "edit",
            "args": {"path": "pricing.py", "oldText": "secret-old", "newText": "secret-new"},
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "tool-1",
            "toolName": "edit",
            "result": {"content": [{"type": "text", "text": "updated pricing.py"}]},
            "isError": False,
        },
    ]
    # 只有非空消息才模拟 Pi 的权威完成事件，空值表示协议结果缺失。
    if final_message:
        events.append(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": final_message}],
                },
            }
        )
    events.append({"type": "future_event", "secret": "must-not-persist"})
    return "\n".join(json.dumps(event) for event in events) + "\nnot-json\n"


class RecordingCommandRunner:
    """模拟 Pi CLI 并记录版本探测与同步执行参数。"""

    def __init__(
        self, *, return_code: int = 0, final_message: str = "done", stderr: str = "pi stderr"
    ) -> None:
        """配置一次可控的 Pi 运行结果。

        参数：
            return_code: Agent 子进程退出码。
            final_message: Pi 权威最终消息；空字符串表示没有产生最终消息。
            stderr: 非零退出时用于诊断的标准错误文本。
        """
        self.return_code = return_code
        self.final_message = final_message
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回固定版本或一次完整的 Pi JSONL 执行结果。

        参数：
            command: Runner 生成的完整命令参数。
            **kwargs: 传给 ``subprocess.run`` 的执行选项。

        返回：
            不访问真实 Pi 和 Ollama 的确定性完成结果。
        """
        self.calls.append((command, kwargs))
        if len(command) == 2 and command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, "0.74.1\n", "")
        return subprocess.CompletedProcess(
            command,
            self.return_code,
            _pi_stdout(final_message=self.final_message),
            self.stderr,
        )


class CompletedStreamingProcess:
    """模拟已经产生完整 stdout/stderr 的 Pi 流式子进程。"""

    def __init__(self, stdout: str, stderr: str = "", return_code: int = 0) -> None:
        """保存可逐行读取的输出及确定性退出码。

        参数：
            stdout: Pi JSONL 标准输出。
            stderr: Pi 诊断标准错误。
            return_code: 已完成进程的退出码。
        """
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = return_code

    def poll(self) -> int:
        """返回已经完成的退出码。"""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """忽略测试超时并返回已经完成的退出码。"""
        del timeout
        return self.returncode

    def terminate(self) -> None:
        """实现流式进程兼容接口；完成态无需改变状态。"""

    def kill(self) -> None:
        """实现流式进程兼容接口；完成态无需改变状态。"""


class RecordingProcessFactory:
    """记录流式启动参数并返回可读取的 Fake Pi 进程。"""

    def __init__(self, stdout: str) -> None:
        """配置 Fake Pi 将要产生的 JSONL 标准输出。

        参数：
            stdout: 流式进程逐行返回的完整文本。
        """
        self.stdout = stdout
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> CompletedStreamingProcess:
        """记录启动参数并返回已经完成的流式进程。

        参数：
            command: Runner 生成的固定 Pi 命令。
            **kwargs: 传给 ``subprocess.Popen`` 的执行选项。

        返回：
            含预置 JSONL 的完成态进程。
        """
        self.calls.append((command, kwargs))
        return CompletedStreamingProcess(self.stdout)


class BrokenAfterHeadersWriter:
    """模拟客户端接收响应头后立即断开连接的写入流。"""

    def __init__(self) -> None:
        """初始化写入次数，用于区分响应头和正文。"""
        self.write_count = 0

    def write(self, data: bytes) -> int:
        """首次接受响应头，后续正文写入稳定抛出 BrokenPipeError。"""
        self.write_count += 1
        if self.write_count > 1:
            raise BrokenPipeError("client disconnected")
        return len(data)

    def flush(self) -> None:
        """实现处理器期望的流接口，不增加额外行为。"""


class FakeProxyResponse:
    """提供一次正文块的固定官方 API 响应。"""

    status = 200

    def __init__(self) -> None:
        """记录正文是否已经返回。"""
        self.sent = False

    def getheaders(self) -> list[tuple[str, str]]:
        """返回无额外响应头的最小集合。"""
        return []

    def read(self, size: int) -> bytes:
        """忽略块大小并只返回一次正文。"""
        del size
        if self.sent:
            return b""
        self.sent = True
        return b"chunk"


class FakeHttpsConnection:
    """替换真实上游连接，使代理断连测试不访问网络。"""

    def __init__(self, host: str, port: int, timeout: int) -> None:
        """接收固定官方连接参数但不建立套接字。"""
        del host, port, timeout

    def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
        """接收代理请求并保持无副作用。"""
        del method, path, body, headers

    def getresponse(self) -> FakeProxyResponse:
        """返回可触发正文 BrokenPipe 的固定响应。"""
        return FakeProxyResponse()

    def close(self) -> None:
        """实现连接关闭接口。"""


class HangingStreamingProcess:
    """模拟输出结束但拒绝优雅退出的 Pi 子进程。"""

    def __init__(self) -> None:
        """初始化空输出和终止调用记录。"""
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        """强制终止前始终报告进程仍在运行。"""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """未 kill 时持续抛出超时，模拟忽略 terminate 的进程。"""
        if self.returncode is None:
            raise subprocess.TimeoutExpired("pi", timeout)
        return self.returncode

    def terminate(self) -> None:
        """记录优雅终止请求但保持运行态。"""
        self.terminated = True

    def kill(self) -> None:
        """记录强制终止并切换到确定退出码。"""
        self.killed = True
        self.returncode = -9


def test_pi_runner_streams_whitelisted_events_counts_tools_and_extracts_message(
    tmp_path: Path,
) -> None:
    """Pi JSONL 应产生安全事件、真实工具计数和权威最终消息。"""
    events: list[dict[str, object]] = []
    process_factory = RecordingProcessFactory(_pi_stdout(final_message="修复完成"))
    runner = PiAgentRunner(
        run_command=RecordingCommandRunner(),
        process_factory=process_factory,
    )

    result = runner.run(
        instruction="Fix the bug",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        workspace=tmp_path,
        timeout_seconds=30,
        on_event=events.append,
    )

    assert [item["event_type"] for item in events] == [
        "agent_session_started",
        "agent_turn_started",
        "tool_started",
        "tool_finished",
        "agent_message",
    ]
    assert result.final_message == "修复完成"
    assert result.event_count == 6
    assert result.tool_call_count == 1
    assert events[2]["actor"] == "pi"
    assert events[3]["payload"]["output"] == "updated pricing.py"
    assert events[3]["payload"]["is_error"] is False
    assert all("secret" not in str(item) for item in events)


def test_pi_runner_uses_project_cli_isolated_config_and_macos_sandbox(tmp_path: Path) -> None:
    """Runner 必须固定项目 Pi、隔离配置、只写工作区且只连本机 Ollama。"""
    command_runner = RecordingCommandRunner()
    runner = PiAgentRunner(run_command=command_runner)

    result = runner.run(
        instruction="Fix the bug",
        model="qwen2.5-coder:7b",
        base_url="http://127.0.0.1:11434/",
        workspace=tmp_path,
        timeout_seconds=30,
    )

    version_command = command_runner.calls[0][0]
    assert version_command[0].endswith("/agent-runtime/node_modules/.bin/pi")
    assert version_command[1:] == ["--version"]
    command, kwargs = command_runner.calls[1]
    assert command[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert "(deny file-write*)" in command[2]
    assert '(subpath (param "WORKSPACE"))' in command[2]
    assert '(remote tcp "localhost:11434")' in command[2]
    assert command[3] == f"-DWORKSPACE={tmp_path}"
    assert command[4] == "--"
    assert command[5].endswith("/agent-runtime/node_modules/.bin/pi")
    assert command[command.index("--mode") + 1] == "json"
    assert command[command.index("--provider") + 1] == "ollama"
    assert command[command.index("--model") + 1] == "qwen2.5-coder:7b"
    assert command[command.index("--tools") + 1] == "read,write,edit,bash"
    assert "--no-session" in command
    assert "--no-extensions" in command
    assert "--no-skills" in command
    assert "--no-prompt-templates" in command
    assert "--no-context-files" in command

    # 配置、临时目录和遥测开关都必须落在样本边界内，不能读取用户全局 Pi 状态。
    environment = kwargs["env"]
    pi_home = Path(environment["PI_CODING_AGENT_DIR"])
    assert pi_home.is_relative_to(tmp_path)
    assert Path(environment["TMPDIR"]).is_relative_to(tmp_path)
    assert environment["PI_OFFLINE"] == "1"
    assert environment["PI_SKIP_VERSION_CHECK"] == "1"
    assert environment["PI_TELEMETRY"] == "0"
    config = json.loads((pi_home / "models.json").read_text(encoding="utf-8"))
    assert config == {
        "providers": {
            "ollama": {
                "baseUrl": "http://127.0.0.1:11434/v1",
                "api": "openai-completions",
                "apiKey": "ollama",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": "qwen2.5-coder:7b"}],
            }
        }
    }
    assert kwargs["cwd"] == tmp_path
    assert result.cli_version == "0.74.1"


def test_pi_runner_hides_deepseek_key_behind_loopback_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek 凭据应留在父进程，Pi 沙箱和工具只能访问本机受控代理。"""
    command_runner = RecordingCommandRunner()
    secret = "sk-deepseek-test-secret"
    monkeypatch.setenv("OTHER_API_KEY", "must-not-reach-agent")
    proxy = (object(), object())
    monkeypatch.setattr(
        pi_module,
        "_start_api_proxy",
        lambda api_key, provider: (
            (*proxy, "http://localhost:49152")
            if api_key == secret and provider == "deepseek"
            else None
        ),
        raising=False,
    )
    monkeypatch.setattr(pi_module, "_stop_api_proxy", lambda *items: None, raising=False)
    runner = PiAgentRunner(
        run_command=command_runner,
        adapter="openai-compatible",
        provider_id="deepseek",
        api_key=secret,
    )

    runner.run(
        instruction="Fix the bug",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        workspace=tmp_path,
        timeout_seconds=30,
    )

    command, kwargs = command_runner.calls[1]
    assert '(remote tcp "localhost:' in command[2]
    assert command[command.index("--provider") + 1] == "deepseek"
    assert command[command.index("--model") + 1] == "deepseek-v4-pro"
    environment = kwargs["env"]
    assert "DEEPSEEK_API_KEY" not in environment
    assert "OTHER_API_KEY" not in environment
    pi_home = Path(environment["PI_CODING_AGENT_DIR"])
    config_text = (pi_home / "models.json").read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert config["providers"]["deepseek"]["baseUrl"].startswith("http://localhost:")
    assert config["providers"]["deepseek"]["apiKey"] == "evalhub-proxy"
    assert config["providers"]["deepseek"]["modelOverrides"] == {
        "deepseek-v4-pro": {"compat": {"supportsDeveloperRole": False}}
    }
    assert secret not in " ".join(command)
    assert secret not in config_text


def test_pi_runner_hides_siliconflow_key_behind_loopback_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SiliconFlow 凭据应留在父进程，Pi 只获得标准 OpenAI 协议的本机代理。"""
    command_runner = RecordingCommandRunner()
    secret = "sk-siliconflow-test-secret"
    proxy = (object(), object())
    monkeypatch.setattr(
        pi_module,
        "_start_api_proxy",
        lambda api_key, provider: (
            (*proxy, "http://localhost:49153")
            if api_key == secret and provider == "siliconflow"
            else None
        ),
        raising=False,
    )
    monkeypatch.setattr(pi_module, "_stop_api_proxy", lambda *items: None, raising=False)
    runner = PiAgentRunner(
        run_command=command_runner,
        adapter="openai-compatible",
        provider_id="siliconflow",
        api_key=secret,
    )

    runner.run(
        instruction="Fix the bug",
        model="moonshotai/Kimi-K2.7-Code",
        base_url="https://api.siliconflow.cn/v1",
        workspace=tmp_path,
        timeout_seconds=30,
    )

    command, kwargs = command_runner.calls[1]
    assert command[command.index("--provider") + 1] == "siliconflow"
    environment = kwargs["env"]
    pi_home = Path(environment["PI_CODING_AGENT_DIR"])
    config_text = (pi_home / "models.json").read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert config == {
        "providers": {
            "siliconflow": {
                "baseUrl": "http://localhost:49153",
                "api": "openai-completions",
                "apiKey": "evalhub-proxy",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": "moonshotai/Kimi-K2.7-Code"}],
            }
        }
    }
    assert "SILICONFLOW_API_KEY" not in environment
    assert secret not in " ".join(command)
    assert secret not in config_text


def test_api_proxy_ignores_client_disconnect_after_response_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pi 超时断开后代理不得再次写 502 并产生 BrokenPipe 堆栈。"""
    monkeypatch.setattr(pi_module.http_client, "HTTPSConnection", FakeHttpsConnection)
    handler_type = pi_module._api_proxy_handler("sk-test", "siliconflow")
    handler = object.__new__(handler_type)
    writer = BrokenAfterHeadersWriter()
    handler.path = "/chat/completions"
    handler.headers = {"Content-Length": "2", "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(b"{}")
    handler.wfile = writer
    handler.request_version = "HTTP/1.1"
    handler.command = "POST"
    handler.requestline = "POST /chat/completions HTTP/1.1"
    handler.close_connection = False

    handler.do_POST()

    assert writer.write_count == 2
    assert handler.close_connection is True


def test_pi_runner_rejects_non_loopback_ollama_before_start(tmp_path: Path) -> None:
    """外部或带凭据的 Ollama 地址不得绕过本机网络沙箱。"""
    runner = PiAgentRunner(run_command=RecordingCommandRunner())

    with pytest.raises(PiAgentError, match="HTTP loopback"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="https://ollama.example.com",
            workspace=tmp_path,
            timeout_seconds=30,
        )


def test_pi_runner_reads_version_from_stderr_like_real_pi_0741() -> None:
    """Pi 0.74.1 的非 TTY 版本输出位于 stderr，Runner 仍应记录版本。"""

    def stderr_version(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟 Pi 0.74.1 的真实版本输出流。

        参数：
            command: Runner 生成的项目内 Pi 版本命令。
            **kwargs: 版本探测的 subprocess 选项，本 Fake 不需要使用。

        返回：
            stdout 为空且 stderr 含版本的成功完成结果。
        """
        del kwargs
        return subprocess.CompletedProcess(command, 0, "", "0.74.1\n")

    assert PiAgentRunner(run_command=stderr_version).version() == "0.74.1"


def test_pi_runner_resolves_relative_workspace_before_building_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对工作区必须先绝对化，避免 Seatbelt 允许错误写入根。"""
    monkeypatch.chdir(tmp_path)
    workspace = Path("relative-workspace")
    workspace.mkdir()
    command_runner = RecordingCommandRunner()

    PiAgentRunner(run_command=command_runner).run(
        instruction="Fix the bug",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        workspace=workspace,
        timeout_seconds=30,
    )

    command, kwargs = command_runner.calls[1]
    assert kwargs["cwd"] == workspace.resolve()
    assert command[3] == f"-DWORKSPACE={workspace.resolve()}"
    assert Path(kwargs["env"]["PI_CODING_AGENT_DIR"]).is_absolute()


def test_pi_runner_rejects_nonzero_exit(tmp_path: Path) -> None:
    """Pi 非零退出应转换为截断且可诊断的 Agent 错误。"""
    runner = PiAgentRunner(run_command=RecordingCommandRunner(return_code=7))

    with pytest.raises(PiAgentError, match="pi exited with code 7: pi stderr"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=30,
        )


def test_pi_runner_classifies_sandbox_exit_as_executor_not_ready(tmp_path: Path) -> None:
    """macOS sandbox-exec 启用失败必须带基础设施分类，不能变成模型运行时错误。"""
    runner = PiAgentRunner(
        run_command=RecordingCommandRunner(
            return_code=71,
            stderr="sandbox-exec: sandbox_apply: Operation not permitted",
        )
    )

    with pytest.raises(PiAgentError, match="sandbox_apply") as raised:
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=30,
        )

    assert raised.value.error_type == "executor_not_ready"


def test_pi_runner_rejects_missing_final_message(tmp_path: Path) -> None:
    """退出成功但没有权威 assistant message_end 时不得伪造完成。"""
    runner = PiAgentRunner(run_command=RecordingCommandRunner(final_message=""))

    with pytest.raises(PiAgentError, match="pi produced no final message"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=30,
        )


def test_pi_runner_force_kills_streaming_process_after_timeout(tmp_path: Path) -> None:
    """Pi 超时且忽略 terminate 时必须继续 kill，避免遗留 Agent 进程。"""
    process = HangingStreamingProcess()
    runner = PiAgentRunner(
        run_command=RecordingCommandRunner(),
        process_factory=lambda command, **kwargs: process,
    )

    with pytest.raises(PiAgentError, match="pi timed out after 0.01 seconds"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=0.01,
        )

    assert process.terminated is True
    assert process.killed is True


def test_pi_runner_terminates_the_real_process_group_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产 Popen 的超时回收必须向整个独立进程组发送 TERM 和 KILL。"""
    process = HangingStreamingProcess()
    process.pid = 43210
    observed_signals: list[tuple[int, signal.Signals]] = []

    def fake_killpg(pid: int, requested_signal: signal.Signals) -> None:
        """记录进程组信号，并在 KILL 时模拟整个进程树已经退出。

        参数：
            pid: 生产 Runner 选择的进程组标识。
            requested_signal: 需要发送给整组的终止信号。
        """
        observed_signals.append((pid, requested_signal))
        if requested_signal == signal.SIGKILL:
            process.returncode = -9

    monkeypatch.setattr(pi_module.os, "killpg", fake_killpg)
    runner = PiAgentRunner(
        run_command=RecordingCommandRunner(),
        process_factory=lambda command, **kwargs: process,
    )

    with pytest.raises(PiAgentError, match="pi timed out"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=0.01,
        )

    assert observed_signals == [(43210, signal.SIGTERM), (43210, signal.SIGKILL)]


def test_pi_runner_converts_sync_timeout(tmp_path: Path) -> None:
    """同步执行器超时应产生稳定错误并保留配置秒数。"""

    def timeout_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """让版本探测成功，并让实际 Pi 执行固定抛出超时。

        参数：
            command: Runner 生成的版本或 Agent 命令。
            **kwargs: 含本次运行 timeout 的子进程选项。

        返回：
            版本探测的成功结果；Agent 分支不返回。

        异常：
            subprocess.TimeoutExpired: 模拟 Pi 超过调用方时限。
        """
        if len(command) == 2 and command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, "0.74.1\n", "")
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    runner = PiAgentRunner(run_command=timeout_runner)
    with pytest.raises(PiAgentError, match="pi timed out after 12 seconds"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=12,
        )
