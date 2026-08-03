"""验证 Codex Agent Runner 的固定命令、结果提取和错误边界。"""

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
