"""验证 Codex Agent Runner 的固定命令、结果提取和错误边界。"""

import io
import json
import subprocess
from pathlib import Path

import pytest

from evalhub.agent.codex import CodexAgentError, CodexAgentRunner


class RecordingCommandRunner:
    """模拟 Codex CLI 并记录所有子进程调用参数。"""

    def __init__(
        self, workspace: Path, *, return_code: int = 0, final_message: str = "done"
    ) -> None:
        """配置工作区、Agent 退出码和可选最终消息。"""
        self.workspace = workspace
        self.return_code = return_code
        self.final_message = final_message
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回固定版本或一次可控的 Codex JSONL 执行结果。"""
        self.calls.append((command, kwargs))
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.test\n", "")

        # 最终消息路径由生产命令显式传递，Fake 只模拟 Codex 的文件副作用。
        output_index = command.index("--output-last-message") + 1
        output_path = Path(command[output_index])
        if self.final_message:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(self.final_message, encoding="utf-8")
        stdout = '{"type":"turn.started"}\n{"type":"turn.completed"}\nnot-json\n'
        return subprocess.CompletedProcess(command, self.return_code, stdout, "codex stderr")


class CompletedStreamingProcess:
    """模拟已经产生完整 stdout/stderr 的 Codex 流式子进程。"""

    def __init__(self, stdout: str, stderr: str = "", return_code: int = 0) -> None:
        """保存可逐行读取的输出及确定性退出码。"""
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
        """记录兼容接口；完成态测试不需要改变状态。"""

    def kill(self) -> None:
        """记录兼容接口；完成态测试不需要改变状态。"""


class RecordingProcessFactory:
    """创建可流式读取的 Fake Codex 进程并写入最终消息文件。"""

    def __init__(self, stdout: str, *, final_message: str = "done") -> None:
        """配置 JSONL stdout 和 Codex 最终消息。"""
        self.stdout = stdout
        self.final_message = final_message
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> CompletedStreamingProcess:
        """记录启动参数、模拟最终消息副作用并返回完成进程。"""
        self.calls.append((command, kwargs))
        output_index = command.index("--output-last-message") + 1
        output_path = Path(command[output_index])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.final_message, encoding="utf-8")
        return CompletedStreamingProcess(self.stdout)


class HangingStreamingProcess:
    """模拟输出流已结束但进程拒绝优雅退出的 Codex 子进程。"""

    def __init__(self) -> None:
        """初始化空输出及终止调用记录。"""
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        """强制终止前始终报告进程仍在运行。"""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """未强制终止时持续抛出超时，模拟忽略 terminate 的进程。"""
        if self.returncode is None:
            raise subprocess.TimeoutExpired("codex exec", timeout)
        return self.returncode

    def terminate(self) -> None:
        """记录优雅终止请求但保持运行态。"""
        self.terminated = True

    def kill(self) -> None:
        """记录强制终止并切换到确定退出码。"""
        self.killed = True
        self.returncode = -9


def test_codex_runner_streams_whitelisted_events_and_counts_tools(tmp_path: Path) -> None:
    """Runner 应逐行输出稳定事件，并忽略未知事件中的任意原始字段。"""
    stdout = (
        '{"type":"thread.started","thread_id":"thread-1"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.started","item":{"id":"item-1","type":"command_execution",'
        '"command":"python -m pytest"}}\n'
        '{"type":"item.completed","item":{"id":"item-1","type":"command_execution",'
        '"command":"python -m pytest","exit_code":0,"aggregated_output":"1 passed"}}\n'
        '{"type":"item.completed","item":{"id":"item-2","type":"agent_message",'
        '"text":"修复完成"}}\n'
        '{"type":"future.event","secret":"must-not-persist"}\n'
    )
    process_factory = RecordingProcessFactory(stdout)
    events: list[dict[str, object]] = []
    runner = CodexAgentRunner(
        run_command=RecordingCommandRunner(tmp_path),
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
    assert result.event_count == 6
    assert result.tool_call_count == 1
    assert events[3]["payload"]["output"] == "1 passed"
    assert all("secret" not in str(item) for item in events)


def test_codex_runner_truncates_persisted_messages_and_tool_output(tmp_path: Path) -> None:
    """超长模型消息和命令输出不得突破审计事件的持久化体积上限。"""
    stdout = (
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "A" * 1_005},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "python -m pytest",
                    "exit_code": 0,
                    "aggregated_output": "B" * 2_005,
                },
            }
        )
        + "\n"
    )
    events: list[dict[str, object]] = []
    runner = CodexAgentRunner(
        run_command=RecordingCommandRunner(tmp_path),
        process_factory=RecordingProcessFactory(stdout),
    )

    runner.run(
        instruction="Fix the bug",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        workspace=tmp_path,
        timeout_seconds=30,
        on_event=events.append,
    )

    assert len(events[0]["payload"]["text"]) == 1_000
    assert len(events[1]["payload"]["output"]) == 2_000


def test_codex_runner_force_kills_streaming_process_after_timeout(tmp_path: Path) -> None:
    """流式进程超过总时限且忽略 terminate 时必须继续 kill，避免遗留 Codex。"""
    process = HangingStreamingProcess()
    runner = CodexAgentRunner(
        run_command=RecordingCommandRunner(tmp_path),
        process_factory=lambda command, **kwargs: process,
    )

    with pytest.raises(CodexAgentError, match="codex timed out after 0.01 seconds"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=0.01,
        )

    assert process.terminated is True
    assert process.killed is True


def test_codex_runner_uses_fixed_local_ollama_scaffold(tmp_path: Path) -> None:
    """Runner 应固定 Codex 本地 Ollama、临时状态和 workspace-write 参数。"""
    command_runner = RecordingCommandRunner(tmp_path)
    runner = CodexAgentRunner(run_command=command_runner)

    result = runner.run(
        instruction="Fix the bug",
        model="qwen2.5-coder:7b",
        base_url="http://127.0.0.1:11434/",
        workspace=tmp_path,
        timeout_seconds=30,
    )

    # 第一次调用只探测版本，第二次必须使用固定且可审计的 Agent 壳参数。
    assert command_runner.calls[0][0] == ["codex", "--version"]
    command, kwargs = command_runner.calls[1]
    assert command[:3] == ["codex", "exec", "--oss"]
    assert command[command.index("--local-provider") + 1] == "ollama"
    assert command[command.index("--model") + 1] == "qwen2.5-coder:7b"
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command

    # Ollama 地址和 Codex 状态目录通过子进程环境注入，不修改用户全局配置。
    environment = kwargs["env"]
    assert environment["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert Path(environment["CODEX_HOME"]).is_relative_to(tmp_path)
    assert kwargs["cwd"] == tmp_path
    assert result.final_message == "done"
    assert result.event_count == 2
    assert result.cli_version == "codex-cli 0.test"


def test_codex_runner_resolves_relative_workspace_before_changing_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对运行目录必须先绝对化，避免 Codex 切换 cwd 后二次解析内部路径。"""
    monkeypatch.chdir(tmp_path)
    workspace = Path("relative-workspace")
    workspace.mkdir()
    command_runner = RecordingCommandRunner(workspace)

    CodexAgentRunner(run_command=command_runner).run(
        instruction="Fix the bug",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        workspace=workspace,
        timeout_seconds=30,
    )

    command, kwargs = command_runner.calls[1]
    environment = kwargs["env"]
    assert kwargs["cwd"] == workspace.resolve()
    assert Path(environment["CODEX_HOME"]).is_absolute()
    output_path = Path(command[command.index("--output-last-message") + 1])
    assert output_path.is_absolute()


def test_codex_runner_rejects_nonzero_exit(tmp_path: Path) -> None:
    """Codex 非零退出应转换为截断且可诊断的 Agent 错误。"""
    runner = CodexAgentRunner(run_command=RecordingCommandRunner(tmp_path, return_code=7))

    with pytest.raises(CodexAgentError, match="codex exited with code 7: codex stderr"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=30,
        )


def test_codex_runner_rejects_missing_final_message(tmp_path: Path) -> None:
    """退出成功但没有最终消息时不得伪造 Agent 已完成。"""
    runner = CodexAgentRunner(run_command=RecordingCommandRunner(tmp_path, final_message=""))

    with pytest.raises(CodexAgentError, match="codex produced no final message"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=30,
        )


def test_codex_runner_converts_timeout(tmp_path: Path) -> None:
    """子进程超时应产生稳定错误并保留配置的秒数。"""

    def timeout_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """版本探测成功，实际 Codex 执行固定抛出超时。"""
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.test\n", "")
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    runner = CodexAgentRunner(run_command=timeout_runner)
    with pytest.raises(CodexAgentError, match="codex timed out after 12 seconds"):
        runner.run(
            instruction="Fix the bug",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            workspace=tmp_path,
            timeout_seconds=12,
        )
