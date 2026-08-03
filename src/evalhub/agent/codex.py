"""运行固定 Codex CLI Agent 壳并提取可评分结果。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol


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


class CommandRunner(Protocol):
    """描述可替换的子进程执行函数，便于测试时隔离真实 Codex。"""

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """执行命令并返回已完成进程；关键字参数遵循 ``subprocess.run``。"""


class CodexAgentRunner:
    """通过受约束的 Codex CLI 命令运行本地 Ollama 模型。"""

    def __init__(self, *, run_command: CommandRunner = subprocess.run) -> None:
        """注入命令执行器；生产环境默认调用 ``subprocess.run``。"""
        self._run_command = run_command
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
    ) -> CodexRunResult:
        """在指定工作区运行固定 Codex Agent 壳。

        参数：
            instruction: 交给 Agent 的单个编码任务说明。
            model: 通过 Ollama 暴露的基模名称。
            base_url: Ollama 服务地址，会写入子进程 ``OLLAMA_HOST``。
            workspace: Agent 唯一可写的样本工作区。
            timeout_seconds: 本次样本允许的最长执行秒数。

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

        # 子进程超时被转换为稳定领域错误，任务中心可以统一标记失败原因。
        started_at = monotonic()
        try:
            completed = self._run_command(
                command,
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CodexAgentError("codex CLI is not installed or not available on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            timeout_label = f"{timeout_seconds:g}"
            raise CodexAgentError(f"codex timed out after {timeout_label} seconds") from exc
        elapsed_seconds = monotonic() - started_at

        # 只有退出成功且落盘了最终消息才算一次可审计的 Agent 执行。
        if completed.returncode != 0:
            detail = _error_detail(completed.stderr)
            raise CodexAgentError(f"codex exited with code {completed.returncode}: {detail}")
        if not output_path.is_file():
            raise CodexAgentError("codex produced no final message")
        final_message = output_path.read_text(encoding="utf-8").strip()
        if not final_message:
            raise CodexAgentError("codex produced no final message")

        return CodexRunResult(
            final_message=final_message,
            event_count=_count_json_events(completed.stdout),
            return_code=completed.returncode,
            wall_time_seconds=elapsed_seconds,
            cli_version=cli_version,
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


def _count_json_events(stdout: str) -> int:
    """统计 JSONL 中可解析为对象的事件行，忽略诊断性非 JSON 输出。"""
    event_count = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            event_count += 1
    return event_count


def _error_detail(stderr: str, *, limit: int = 2_000) -> str:
    """截断 CLI 错误文本，既保留诊断信息又限制任务结果体积。"""
    detail = stderr.strip() or "no stderr"
    if len(detail) <= limit:
        return detail
    return detail[-limit:]
