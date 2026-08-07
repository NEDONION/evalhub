"""验证 MiniClaw 子进程桥的命令、JSONL、错误和事件脱敏边界。"""

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import evalhub.agent.miniclaw_bridge as bridge_module
from evalhub.agent.base import AgentRunError, AgentTraceEvent

try:
    from evalhub.agent.miniclaw import MiniClawAgentRunner, resolve_miniclaw_root
    from evalhub.agent.miniclaw_bridge import normalize_event
except ModuleNotFoundError:
    MiniClawAgentRunner = None  # type: ignore[assignment,misc]
    resolve_miniclaw_root = None  # type: ignore[assignment]
    normalize_event = None  # type: ignore[assignment]


class RecordingInput(io.StringIO):
    """保留 close 前写入的 stdin，供测试验证任务说明没有进入 argv。"""

    def __init__(self) -> None:
        """初始化空输入流和关闭标记。"""
        super().__init__()
        self.was_closed = False

    def close(self) -> None:
        """只记录关闭动作，使测试结束时仍能读取已写 JSON。"""
        self.was_closed = True


class FakeStreamingProcess:
    """模拟已经产生有限 stdout/stderr 的 MiniClaw 子进程。"""

    def __init__(
        self,
        stdout: str,
        *,
        stderr: str = "",
        return_code: int | None = 0,
    ) -> None:
        """保存受控进程流和退出状态。

        Args:
            stdout: Runner 将逐行解析的 JSONL 文本。
            stderr: 非零退出时存在但不得直接泄露的诊断文本。
            return_code: ``None`` 表示模拟仍在运行的进程。
        """
        self.stdin = RecordingInput()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = return_code
        self.pid = 998_877
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        """返回当前受控退出状态。"""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """完成进程或在挂起场景抛出标准超时。"""
        if self.returncode is None:
            raise subprocess.TimeoutExpired("miniclaw", timeout)
        return self.returncode

    def terminate(self) -> None:
        """记录普通终止并设置负退出码。"""
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        """记录强制终止并设置负退出码。"""
        self.killed = True
        self.returncode = -9


class RecordingProcessFactory:
    """记录 Runner 使用的固定命令并返回预置进程。"""

    def __init__(self, process: FakeStreamingProcess) -> None:
        """保存唯一将返回的 Fake 进程。"""
        self.process = process
        self.command: list[str] | None = None
        self.kwargs: dict[str, object] = {}

    def __call__(self, command: list[str], **kwargs: object) -> FakeStreamingProcess:
        """记录不经 shell 的启动参数并返回受控进程。"""
        self.command = command
        self.kwargs = kwargs
        return self.process


def _project(root: Path) -> Path:
    """创建只包含可执行 Python 边界的最小 MiniClaw 项目目录。"""
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    return root


def _describe(**overrides: object) -> subprocess.CompletedProcess[str]:
    """构造与真实 describe 完整字段一致的成功命令结果。"""
    payload: dict[str, object] = {
        "available": True,
        "version": "0.1.0",
        "model": "agent-model",
        "runtime_fingerprint": "sha256:test",
        "message": "ready",
    }
    payload.update(overrides)
    return subprocess.CompletedProcess([], 0, json.dumps(payload), "")


def _runner_class():
    """返回待实现 Runner 类型，使缺失模块表现为明确 RED 断言。"""
    assert MiniClawAgentRunner is not None, "MiniClawAgentRunner is not implemented"
    return MiniClawAgentRunner


def test_miniclaw_root_prefers_explicit_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """部署显式路径必须覆盖默认同级发现，避免调用错误的 Agent 项目。"""
    assert resolve_miniclaw_root is not None, "resolve_miniclaw_root is not implemented"
    root = tmp_path / "miniclaw"
    monkeypatch.setenv("EVALHUB_MINICLAW_ROOT", str(root))

    assert resolve_miniclaw_root() == root.resolve()


def test_miniclaw_runner_streams_events_and_keeps_instruction_out_of_argv(
    tmp_path: Path,
) -> None:
    """Runner 必须逐条转发事件，并仅通过 stdin 发送公开任务说明。"""
    process = FakeStreamingProcess(
        "".join(
            (
                '{"type":"event","event":{"event_type":"tool_started",'
                '"actor":"miniclaw","message":null,'
                '"payload":{"tool_name":"read_file"}}}\n',
                '{"type":"result","final_message":"done","tool_call_count":1,'
                '"version":"0.1.0","model":"agent-model"}\n',
            )
        )
    )
    factory = RecordingProcessFactory(process)
    runner = _runner_class()(
        root=_project(tmp_path / "miniclaw"),
        run_command=lambda *args, **kwargs: _describe(),
        process_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[AgentTraceEvent] = []

    result = runner.run(
        instruction="repair the project",
        workspace=workspace,
        timeout_seconds=5,
        on_event=events.append,
    )

    assert result.final_message == "done"
    assert result.tool_call_count == 1
    assert events == [
        {
            "event_type": "tool_started",
            "actor": "miniclaw",
            "message": None,
            "payload": {"tool_name": "read_file"},
        }
    ]
    assert factory.command is not None
    assert "repair the project" not in factory.command
    assert factory.kwargs["shell"] is False
    assert json.loads(process.stdin.getvalue()) == {"instruction": "repair the project"}
    assert process.stdin.was_closed is True


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not-json\n", "invalid MiniClaw bridge output"),
        ('{"type":"event","event":{}}\n', "produced no terminal result"),
        (
            '{"type":"result","final_message":"one","tool_call_count":0,'
            '"version":"0.1.0","model":"m"}\n'
            '{"type":"result","final_message":"two","tool_call_count":0,'
            '"version":"0.1.0","model":"m"}\n',
            "produced multiple terminal results",
        ),
    ],
)
def test_miniclaw_runner_rejects_malformed_jsonl(
    tmp_path: Path,
    stdout: str,
    message: str,
) -> None:
    """无效 JSON、终态缺失或重复都不能被当成一次成功 Agent 运行。"""
    runner = _runner_class()(
        root=_project(tmp_path / "miniclaw"),
        run_command=lambda *args, **kwargs: _describe(),
        process_factory=RecordingProcessFactory(FakeStreamingProcess(stdout)),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(AgentRunError, match=message):
        runner.run(instruction="repair", workspace=workspace, timeout_seconds=5)


def test_miniclaw_runner_does_not_echo_bridge_error_or_stderr(tmp_path: Path) -> None:
    """Provider 错误和 stderr 中的假密钥都必须收窄为稳定安全消息。"""
    process = FakeStreamingProcess(
        '{"type":"error","code":"provider_error",'
        '"message":"request failed with sk-private-test"}\n',
        stderr="debug sk-private-test",
        return_code=7,
    )
    runner = _runner_class()(
        root=_project(tmp_path / "miniclaw"),
        run_command=lambda *args, **kwargs: _describe(),
        process_factory=RecordingProcessFactory(process),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(AgentRunError) as captured:
        runner.run(instruction="repair", workspace=workspace, timeout_seconds=5)

    assert str(captured.value) == "MiniClaw provider request failed"
    assert "sk-private-test" not in str(captured.value)


def test_miniclaw_metadata_blocks_unavailable_runtime(tmp_path: Path) -> None:
    """未初始化或缺少凭据的 MiniClaw 必须成为基础设施阻塞而非零分。"""
    runner = _runner_class()(
        root=_project(tmp_path / "miniclaw"),
        run_command=lambda *args, **kwargs: _describe(
            available=False,
            version=None,
            model=None,
            runtime_fingerprint=None,
            message="MiniClaw API key is not configured",
        ),
    )

    with pytest.raises(AgentRunError) as captured:
        runner.metadata()

    assert captured.value.error_type == "executor_not_ready"
    assert str(captured.value) == "MiniClaw API key is not configured"


def test_miniclaw_bridge_normalizes_only_whitelisted_event_fields() -> None:
    """桥不得持久化 reasoning 或未登记动态字段，并应截断工具预览。"""
    assert normalize_event is not None, "normalize_event is not implemented"
    reasoning = normalize_event("model_reasoning", {"text": "private chain"})
    tool = normalize_event(
        "tool_finished",
        {
            "call_id": "call-1",
            "tool_name": "read_file",
            "status": "completed",
            "result_preview": "x" * 2_000,
            "secret": "must-not-pass",
        },
    )

    assert reasoning is None
    assert tool is not None
    assert set(tool["payload"]) == {"call_id", "tool_name", "status", "result_preview"}
    assert len(cast(str, tool["payload"]["result_preview"])) == 1_000


def test_bridge_removes_its_directory_before_importing_external_miniclaw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """脚本目录中的 Runner 文件不得遮蔽外部 MiniClaw 安装包。"""
    bridge_directory = str(Path(bridge_module.__file__).resolve().parent)
    monkeypatch.setattr(sys, "path", [bridge_directory, "/external/site-packages"])
    prepare_import_path = getattr(bridge_module, "prepare_miniclaw_import_path", None)

    assert prepare_import_path is not None, "prepare_miniclaw_import_path is not implemented"
    prepare_import_path()
    assert bridge_directory not in sys.path
