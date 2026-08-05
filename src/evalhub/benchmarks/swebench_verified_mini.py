"""冻结 SWE-bench Verified Mini 六题及官方 gold 就绪证明。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from evalhub.benchmarks.readiness import ExecutorReadiness

SUITE_VERSION = "swebench-verified-mini-v1"
DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
HARNESS_REVISION = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
INSTANCE_IDS = (
    "psf__requests-2931",
    "psf__requests-6028",
    "pydata__xarray-2905",
    "pydata__xarray-7229",
    "pytest-dev__pytest-7324",
    "pytest-dev__pytest-10356",
)
_RUN_ID = "evalhub-swebench-verified-mini-gold"
_RUNTIME_NAME = "swebench-verified-mini"
_PREPARE_COMMAND = "./scripts/prepare_swebench_verified_mini.sh"

# 清单摘要包含套件、数据集、Harness 提交和有序实例，任一变化都必须发布新版本。
_MANIFEST = {
    "dataset": DATASET_NAME,
    "harness_revision": HARNESS_REVISION,
    "instance_ids": list(INSTANCE_IDS),
    "suite_version": SUITE_VERSION,
}
MANIFEST_SHA256 = hashlib.sha256(
    json.dumps(_MANIFEST, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def gold_validation_command(project_root: Path) -> list[str]:
    """构造只运行冻结六题官方 gold patch 的 Harness 命令。

    参数：
        project_root: EvalHub 仓库根目录，用于定位隔离安装的官方 Harness。

    返回：
        可直接交给 ``subprocess`` 且不经过 shell 的固定参数列表。
    """
    harness_python = project_root / ".runtime" / _RUNTIME_NAME / "venv/bin/python"
    return [
        str(harness_python),
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        DATASET_NAME,
        "--predictions_path",
        "gold",
        "--instance_ids",
        *INSTANCE_IDS,
        "--max_workers",
        "2",
        "--run_id",
        _RUN_ID,
        "--cache_level",
        "env",
        "--clean",
        "True",
    ]


def write_gold_marker(report_path: Path, marker_path: Path) -> dict[str, object]:
    """验证官方汇总报告为冻结六题 6/6，并原子写入本机就绪证明。

    参数：
        report_path: 官方 Harness 生成的 ``gold.<run_id>.json``。
        marker_path: EvalHub 运行时目录内的就绪证明路径。

    返回：
        已写入 marker 的 JSON 兼容内容。

    异常：
        ValueError: 报告格式错误、存在执行错误或任一冻结实例未解决。
        OSError: 报告或 marker 无法读写。
    """
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("SWE-bench gold report is not valid JSON") from exc
    if not isinstance(report, Mapping):
        raise ValueError("SWE-bench gold report must be a JSON object")

    # 只接受精确六题全部完成且全部 resolved；额外或缺失实例都会使版本证明失效。
    resolved_ids = report.get("resolved_ids")
    completed = report.get("completed_instances")
    errors = report.get("error_instances")
    valid_ids = set(resolved_ids or ()) == set(INSTANCE_IDS)
    if completed != len(INSTANCE_IDS) or errors != 0 or not valid_ids:
        raise ValueError("SWE-bench gold validation must resolve all 6 frozen instances")
    marker: dict[str, object] = {
        "suite_version": SUITE_VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "harness_revision": HARNESS_REVISION,
        "resolved_ids": list(INSTANCE_IDS),
        "validated_at": datetime.now(UTC).isoformat(),
    }

    # 临时文件与目标位于同一目录，replace 可避免进程中断留下半个 JSON 证明。
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = marker_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(marker_path)
    return marker


def gold_readiness(
    project_root: Path,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> ExecutorReadiness:
    """检查 Docker、固定 Harness 和 6/6 gold 证明是否同时就绪。

    参数：
        project_root: EvalHub 仓库根目录。
        command_runner: 可替换的 Docker 只读探测函数。

    返回：
        全部证明齐备时为 ready，否则给出不会计模型零分的阻塞原因。
    """
    if not _docker_is_ready(command_runner):
        return ExecutorReadiness(
            False,
            "executor_not_ready",
            f"Docker 服务不可用；请运行 {_PREPARE_COMMAND}",
        )
    runtime_root = project_root / ".runtime" / _RUNTIME_NAME
    if not (runtime_root / "venv/bin/python").is_file():
        return ExecutorReadiness(
            False,
            "executor_not_ready",
            f"SWE-bench 官方 Harness 未安装；请运行 {_PREPARE_COMMAND}",
        )

    # marker 只属于当前固定清单；旧版本、损坏 JSON 或非 6/6 证明一律重新验证。
    marker_path = runtime_root / "gold-validation.json"
    marker = _read_marker(marker_path)
    if not _marker_matches_manifest(marker):
        return ExecutorReadiness(
            False,
            "executor_not_ready",
            f"SWE-bench 六题 gold 证明缺失或过期；请运行 {_PREPARE_COMMAND}",
        )
    return ExecutorReadiness(True, "ready", "SWE-bench Verified Mini 官方 gold 6/6 已就绪")


def _docker_is_ready(command_runner: CommandRunner) -> bool:
    """用 Docker Server 版本探测确认 CLI 与守护进程同时可用。"""
    try:
        completed = command_runner(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0


def _read_marker(marker_path: Path) -> Mapping[str, object] | None:
    """读取本机 gold marker，缺失、损坏或非对象内容均返回空值。"""
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return marker if isinstance(marker, Mapping) else None


def _marker_matches_manifest(marker: Mapping[str, object] | None) -> bool:
    """判断 marker 是否精确对应当前清单且包含全部六个 resolved ID。"""
    if marker is None:
        return False
    return (
        marker.get("suite_version") == SUITE_VERSION
        and marker.get("manifest_sha256") == MANIFEST_SHA256
        and marker.get("harness_revision") == HARNESS_REVISION
        and tuple(marker.get("resolved_ids", ())) == INSTANCE_IDS
    )


def main(argv: Sequence[str] | None = None) -> int:
    """提供准备脚本使用的最小 marker 写入命令。

    参数：
        argv: 可选命令行参数；默认读取当前进程参数。

    返回：
        marker 成功写入时返回零。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("record-gold", nargs="?")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    arguments = parser.parse_args(argv)
    write_gold_marker(arguments.report, arguments.marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
