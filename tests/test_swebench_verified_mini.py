"""验证固定 SWE-bench Verified Mini 清单与 gold 就绪门槛。"""

import json
import subprocess
from pathlib import Path

import pytest

from evalhub.benchmarks.swebench_verified_mini import (
    HARNESS_REVISION,
    INSTANCE_IDS,
    MANIFEST_SHA256,
    SUITE_VERSION,
    gold_readiness,
    gold_validation_command,
    write_gold_marker,
)


def test_swebench_verified_mini_manifest_is_frozen() -> None:
    """独立真实套件必须固定六题、官方 Harness 提交和清单摘要。"""
    assert SUITE_VERSION == "swebench-verified-mini-v1"
    assert HARNESS_REVISION == "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
    assert INSTANCE_IDS == (
        "psf__requests-2931",
        "psf__requests-6028",
        "pydata__xarray-2905",
        "pydata__xarray-7229",
        "pytest-dev__pytest-7324",
        "pytest-dev__pytest-10356",
    )
    assert MANIFEST_SHA256 == "82016ccf67ab077a886ee1d3f887996bc011a228f9f6be26852d640021357253"


def test_swebench_gold_command_uses_official_harness_and_six_ids(tmp_path: Path) -> None:
    """gold 命令应调用固定环境中的官方模块，并只运行冻结的六个实例。"""
    command = gold_validation_command(tmp_path)

    assert command[:3] == [
        str(tmp_path / ".runtime/swebench-verified-mini/venv/bin/python"),
        "-m",
        "swebench.harness.run_evaluation",
    ]
    assert command[command.index("--dataset_name") + 1] == "princeton-nlp/SWE-bench_Verified"
    start = command.index("--instance_ids") + 1
    end = command.index("--max_workers")
    assert command[start:end] == list(INSTANCE_IDS)
    assert command[command.index("--predictions_path") + 1] == "gold"


def test_swebench_gold_marker_requires_all_six_resolved(tmp_path: Path) -> None:
    """官方报告只要缺少一个 resolved 实例就不得写入就绪证明。"""
    report_path = tmp_path / "gold.json"
    marker_path = tmp_path / "marker.json"
    report_path.write_text(
        json.dumps(
            {
                "completed_instances": 6,
                "resolved_instances": 5,
                "resolved_ids": list(INSTANCE_IDS[:-1]),
                "error_instances": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="all 6 frozen instances"):
        write_gold_marker(report_path, marker_path)
    assert not marker_path.exists()


def test_swebench_readiness_requires_docker_harness_and_valid_gold_marker(
    tmp_path: Path,
) -> None:
    """Docker、固定 Harness 和 6/6 gold 证明缺一都应阻塞而非产生零分。"""
    def docker_down(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟 Docker 守护进程不可用，并忽略只读探测参数。"""
        del kwargs
        return subprocess.CompletedProcess(command, 1, "", "")

    assert gold_readiness(tmp_path, command_runner=docker_down).code == "executor_not_ready"

    harness_python = tmp_path / ".runtime/swebench-verified-mini/venv/bin/python"
    harness_python.parent.mkdir(parents=True)
    harness_python.write_text("", encoding="utf-8")
    def docker_up(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟 Docker Server 正常返回固定版本。"""
        del kwargs
        return subprocess.CompletedProcess(command, 0, "29.6.2", "")

    missing_gold = gold_readiness(tmp_path, command_runner=docker_up)
    assert missing_gold.ready is False
    assert "gold" in missing_gold.message

    report_path = tmp_path / "gold.json"
    report_path.write_text(
        json.dumps(
            {
                "completed_instances": 6,
                "resolved_instances": 6,
                "resolved_ids": list(INSTANCE_IDS),
                "error_instances": 0,
            }
        ),
        encoding="utf-8",
    )
    marker_path = tmp_path / ".runtime/swebench-verified-mini/gold-validation.json"
    write_gold_marker(report_path, marker_path)

    readiness = gold_readiness(tmp_path, command_runner=docker_up)
    assert readiness.ready is True
    assert readiness.code == "ready"
