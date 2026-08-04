"""统一报告原生、未支持和固定 Docker HumanEval 执行器就绪状态。"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from evalhub.benchmarks.humaneval import (
    DockerHumanEvalSandbox,
    SandboxInfrastructureError,
    resolve_humaneval_image,
)
from evalhub.benchmarks.models import BenchmarkSpec, ExecutorKind

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_BUILD_COMMAND = "./scripts/build_humaneval_image.sh"


@dataclass(frozen=True)
class ExecutorReadiness:
    """保存执行器是否可用、稳定状态码和可操作的短说明。"""

    ready: bool
    code: str
    message: str


def benchmark_readiness(
    spec: BenchmarkSpec,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> ExecutorReadiness:
    """按 Benchmark 执行器类型返回真实就绪状态，不自动构建或下载镜像。

    Args:
        spec: Registry 中待检查的不可变 Benchmark 规格。
        command_runner: 用于探测 Docker 服务和固定镜像的可替换命令边界。

    Returns:
        原生执行器直接就绪；Hexagon HumanEval 需 Docker 服务和固定镜像均成功；
        其他尚未接通的执行器返回 ``executor_not_ready``。
    """
    if spec.executor == ExecutorKind.NATIVE:
        return ExecutorReadiness(True, "ready", "本地原生执行器已就绪")
    if spec.id != "hexagon-humaneval" or spec.executor != ExecutorKind.SANDBOXED_CODE:
        return ExecutorReadiness(False, "executor_not_ready", _unsupported_message(spec))

    # ``docker version`` 同时证明 CLI 与守护进程可用；只找到命令文件不算就绪。
    if not _command_succeeds(
        ["docker", "version", "--format", "{{.Server.Version}}"], command_runner
    ):
        return ExecutorReadiness(
            False,
            "executor_not_ready",
            f"Docker 服务不可用；构建并启动固定镜像：{_BUILD_COMMAND}",
        )
    image = DockerHumanEvalSandbox.image
    try:
        resolve_humaneval_image(command_runner)
    except SandboxInfrastructureError:
        return ExecutorReadiness(
            False,
            "executor_not_ready",
            f"HumanEval 固定镜像 {image} 缺失或配置不可信；请运行 {_BUILD_COMMAND}",
        )
    return ExecutorReadiness(True, "ready", f"HumanEval 固定镜像 {image} 已就绪")


def _command_succeeds(command: list[str], command_runner: CommandRunner) -> bool:
    """以短超时执行只读探测，并把缺失、超时和非零退出统一视为失败。

    Args:
        command: 不经 shell 执行的固定 Docker 探测参数。
        command_runner: 接收 ``subprocess.run`` 兼容参数的命令边界。

    Returns:
        仅当命令在五秒内以零状态结束时返回 ``True``。
    """
    try:
        completed = command_runner(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0


def _unsupported_message(spec: BenchmarkSpec) -> str:
    """为未接通执行器返回稳定说明，保持现有 Registry 的可诊断语义。

    Args:
        spec: 当前不可运行的 Benchmark 规格。

    Returns:
        不包含外部命令输出的中文短说明。
    """
    executor_name = {
        ExecutorKind.LM_EVAL: "lm_eval",
        ExecutorKind.SANDBOXED_CODE: "代码沙箱",
    }.get(spec.executor, spec.executor.value)
    return f"{executor_name} 执行器尚未配置"
