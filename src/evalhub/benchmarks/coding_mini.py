"""提供可离线验证的 Codex Coding Mini Agent Benchmark。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from evalhub.agent.codex import CodexAgentError, CodexAgentRunner, CodexRunResult

CAPABILITY_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("planning", "规划"),
    ("code_understanding", "代码理解"),
    ("implementation", "实现正确性"),
    ("tool_use", "工具使用"),
    ("verification", "验证能力"),
    ("robustness", "稳健性"),
)

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class CodingAgentSample:
    """描述一个包含初始仓库、任务说明、隐藏校验和能力权重的编码样本。"""

    id: str
    instruction: str
    files: Mapping[str, str]
    verifier_code: str
    capability_weights: Mapping[str, float]


class AgentRunner(Protocol):
    """定义 Coding Mini 对固定 Agent 壳所需的最小接口。"""

    def version(self) -> str:
        """返回 Agent 壳版本，供最终结果审计。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CodexRunResult:
        """在隔离工作区执行一个编码样本并返回运行元数据。"""


def coding_mini_samples() -> tuple[CodingAgentSample, ...]:
    """返回内置 Coding Mini 样本。

    三条 quick 样本共同覆盖六项能力。任务与初始文件可以交给 Agent，隐藏校验代码
    只在 Agent 退出后由 EvalHub 执行，不写入样本仓库。
    """
    return (
        CodingAgentSample(
            id="pricing_total",
            instruction=(
                "Fix pricing.total_with_tax so every supplied price participates in the "
                "subtotal. Preserve the public function signature, handle an empty list, "
                "and verify the change."
            ),
            files={
                "pricing.py": (
                    "def total_with_tax(prices, tax_rate):\n"
                    "    subtotal = sum(prices[:-1])\n"
                    "    return round(subtotal * (1 + tax_rate), 2)\n"
                )
            },
            verifier_code=(
                "from pricing import total_with_tax\n"
                "assert total_with_tax([10, 20], 0.1) == 33.0\n"
                "assert total_with_tax([8], 0.25) == 10.0\n"
                "assert total_with_tax([], 0.2) == 0.0\n"
            ),
            capability_weights={
                "planning": 0.4,
                "code_understanding": 0.4,
                "implementation": 0.2,
            },
        ),
        CodingAgentSample(
            id="slug_normalization",
            instruction=(
                "Improve normalize_slug in slug.py. The result must be lowercase ASCII words "
                "joined by one hyphen; whitespace and punctuation are separators and empty "
                "input returns ''. Keep the function signature and verify representative "
                "edge cases."
            ),
            files={
                "slug.py": (
                    "def normalize_slug(value):\n"
                    "    return value.lower().replace(' ', '-')\n"
                )
            },
            verifier_code=(
                "from slug import normalize_slug\n"
                "assert normalize_slug('Hello,  World!') == 'hello-world'\n"
                "assert normalize_slug('  API_v2 / Ready ') == 'api-v2-ready'\n"
                "assert normalize_slug('') == ''\n"
            ),
            capability_weights={
                "implementation": 0.35,
                "tool_use": 0.35,
                "verification": 0.3,
            },
        ),
        CodingAgentSample(
            id="inventory_reservation",
            instruction=(
                "Repair inventory.reserve without changing its signature. A reservation "
                "succeeds only for a positive quantity with enough stock; every rejected "
                "request must leave stock unchanged, including unknown items. Verify both "
                "success and failure paths."
            ),
            files={
                "inventory.py": (
                    "def reserve(stock, item, quantity):\n"
                    "    stock[item] -= quantity\n"
                    "    return stock[item] >= 0\n"
                )
            },
            verifier_code=(
                "from inventory import reserve\n"
                "stock = {'pen': 3}\n"
                "assert reserve(stock, 'pen', 2) is True and stock == {'pen': 1}\n"
                "assert reserve(stock, 'pen', 2) is False and stock == {'pen': 1}\n"
                "assert reserve(stock, 'missing', 1) is False and 'missing' not in stock\n"
                "assert reserve(stock, 'pen', 0) is False and stock == {'pen': 1}\n"
            ),
            capability_weights={
                "code_understanding": 0.2,
                "tool_use": 0.1,
                "verification": 0.3,
                "robustness": 0.4,
            },
        ),
    )


def run_codex_agent_benchmark(
    *,
    job_id: str,
    model: str,
    base_url: str,
    limit: int | None,
    on_progress: ProgressCallback | None = None,
    runner: AgentRunner | None = None,
    runtime_root: Path = Path(".runtime/agent-runs"),
) -> dict[str, object]:
    """运行 Coding Mini 并聚合六维 Agent 能力报告。

    参数：
        job_id: 任务中心生成的唯一标识，用作运行目录名。
        model: Codex 本地 Ollama Provider 使用的基模。
        base_url: Ollama 服务根地址。
        limit: 最多运行的内置样本数；为空时运行全部样本。
        on_progress: 接收已完成样本数和总数的可选回调。
        runner: 可替换的 Agent 壳；默认创建真实 ``CodexAgentRunner``。
        runtime_root: 所有 Agent 样本工作区的父目录。

    返回：
        与普通评测公共字段兼容，并包含 Agent 元数据、样本结果和六维报告的字典。

    异常：
        ValueError: 任务标识或样本上限无效。
        RuntimeError: 无法创建 Git 工作区或执行隐藏 Verifier。
        CodexAgentError: 无法探测 Codex CLI 版本。
    """
    selected_samples = _select_samples(limit)
    job_root = _job_root(runtime_root, job_id)
    active_runner = runner or CodexAgentRunner()
    cli_version = active_runner.version()

    # 先公布真实分母，页面在第一个 Agent 样本运行期间也能显示确定进度。
    total_samples = len(selected_samples)
    if on_progress is not None:
        on_progress(0, total_samples)
    sample_results: list[dict[str, object]] = []

    # 每个样本拥有独立初始提交；某个 Codex 失败不会阻断后续能力维度采样。
    for completed, sample in enumerate(selected_samples, start=1):
        workspace = _create_workspace(job_root, sample)
        sample_results.append(
            _run_sample(
                sample=sample,
                workspace=workspace,
                model=model,
                base_url=base_url,
                runner=active_runner,
            )
        )
        if on_progress is not None:
            on_progress(completed, total_samples)

    # 样本得分与能力得分都来自隐藏校验，不读取或相信 Agent 的最终自然语言声明。
    passed_samples = sum(result["status"] == "success" for result in sample_results)
    dimensions = _aggregate_dimensions(selected_samples, sample_results)
    overall_score = round(
        sum(float(item["score"]) for item in dimensions) / len(dimensions), 4
    )
    failed_ids = [
        str(result["sample_id"])
        for result in sample_results
        if result["status"] != "success"
    ]

    return {
        "job_id": job_id,
        "status": "success",
        "evaluation_type": "agent",
        "dataset": "coding_mini",
        "benchmark": "EvalHub Coding Mini",
        "model": model,
        "adapter": "ollama",
        "metric": "hidden_verifier_pass_rate",
        "total_samples": total_samples,
        "passed_samples": passed_samples,
        "average_score": round(passed_samples / total_samples, 4),
        "failed_sample_ids": failed_ids,
        "failed_examples": _failed_examples(sample_results),
        "agent": {
            "framework": "codex",
            "cli_version": cli_version,
            "scaffold_hash": _scaffold_hash(selected_samples),
        },
        "capability_report": {
            "overall_score": overall_score,
            "dimensions": dimensions,
        },
        "sample_results": sample_results,
    }


def _select_samples(limit: int | None) -> tuple[CodingAgentSample, ...]:
    """按请求上限选择稳定前缀，并拒绝无法产生报告的非正上限。"""
    samples = coding_mini_samples()
    if limit is None:
        return samples
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    return samples[:limit]


def _job_root(runtime_root: Path, job_id: str) -> Path:
    """解析任务运行目录并拒绝可能逃逸父目录的任务标识。"""
    if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
        raise ValueError("job_id must be a safe path segment")
    job_root = runtime_root / job_id
    job_root.mkdir(parents=True, exist_ok=False)
    return job_root


def _create_workspace(job_root: Path, sample: CodingAgentSample) -> Path:
    """写入样本初始文件并创建本地 Git 基线提交。"""
    workspace = job_root / sample.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)

    # 样本文件名由内置定义控制，但仍验证相对路径以防未来编辑时引入目录逃逸。
    for relative_name, content in sample.files.items():
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"unsafe sample file path: {relative_name}")
        destination = workspace / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    # 初始提交让 Codex 可以用标准 Git 工具观察自己的变更，身份只对本次提交生效。
    _run_setup_command(["git", "init", "--quiet"], workspace)
    _run_setup_command(["git", "add", "."], workspace)
    _run_setup_command(
        [
            "git",
            "-c",
            "user.name=EvalHub",
            "-c",
            "user.email=evalhub@localhost",
            "commit",
            "--quiet",
            "-m",
            "Initial benchmark fixture",
        ],
        workspace,
    )
    return workspace


def _run_setup_command(command: list[str], workspace: Path) -> None:
    """执行 Git 初始化命令，并把环境错误转换为可诊断的任务级异常。"""
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"failed to initialize benchmark workspace: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise RuntimeError(f"failed to initialize benchmark workspace: {detail[-1000:]}")


def _run_sample(
    *,
    sample: CodingAgentSample,
    workspace: Path,
    model: str,
    base_url: str,
    runner: AgentRunner,
) -> dict[str, object]:
    """运行单条 Agent 样本，再以隐藏 Verifier 判定唯一分数。"""
    try:
        run_result = runner.run(
            instruction=sample.instruction,
            model=model,
            base_url=base_url,
            workspace=workspace,
            timeout_seconds=180,
        )
    except CodexAgentError as exc:
        # CLI 层错误只降低当前样本分数，使其余样本仍能提供能力诊断。
        return {
            "sample_id": sample.id,
            "status": "failed",
            "score": 0.0,
            "final_message": "",
            "event_count": 0,
            "wall_time_seconds": 0.0,
            "verifier_message": str(exc),
        }

    verifier_passed, verifier_message = _verify_workspace(sample, workspace)
    return {
        "sample_id": sample.id,
        "status": "success" if verifier_passed else "failed",
        "score": 1.0 if verifier_passed else 0.0,
        "final_message": run_result.final_message[:1000],
        "event_count": run_result.event_count,
        "wall_time_seconds": round(run_result.wall_time_seconds, 3),
        "verifier_message": verifier_message,
    }


def _verify_workspace(sample: CodingAgentSample, workspace: Path) -> tuple[bool, str]:
    """在 Agent 退出后执行未暴露给它的 Python 断言代码。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", sample.verifier_code],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"hidden verifier could not execute for {sample.id}: {exc}") from exc

    # 非零退出代表实现不满足断言，是样本失败而不是评测平台故障。
    if completed.returncode == 0:
        return True, "hidden verifier passed"
    detail = completed.stderr.strip() or completed.stdout.strip() or "hidden verifier failed"
    return False, detail[-1000:]


def _aggregate_dimensions(
    samples: tuple[CodingAgentSample, ...], sample_results: list[dict[str, object]]
) -> list[dict[str, object]]:
    """按样本声明权重计算固定顺序的六维归一化分数。"""
    passed_by_id = {
        str(result["sample_id"]): result["status"] == "success" for result in sample_results
    }
    dimensions: list[dict[str, object]] = []

    # 分母只累计实际选中样本，保证未来按 limit 运行时报告仍可解释。
    for key, label in CAPABILITY_DIMENSIONS:
        total_weight = sum(float(sample.capability_weights.get(key, 0.0)) for sample in samples)
        passed_weight = sum(
            float(sample.capability_weights.get(key, 0.0))
            for sample in samples
            if passed_by_id[sample.id]
        )
        score = passed_weight / total_weight if total_weight else 0.0
        dimensions.append({"key": key, "label": label, "score": round(score, 4)})
    return dimensions


def _failed_examples(sample_results: list[dict[str, object]]) -> list[dict[str, object]]:
    """提取简短失败摘要，供现有通用结果详情兼容展示。"""
    return [
        {
            "sample_id": result["sample_id"],
            "score": result["score"],
            "reason": result["verifier_message"],
        }
        for result in sample_results
        if result["status"] != "success"
    ]


def _scaffold_hash(samples: tuple[CodingAgentSample, ...]) -> str:
    """计算样本定义哈希，标识生成报告所用的固定 Agent 评测脚手架。"""
    payload = [
        {
            "id": sample.id,
            "instruction": sample.instruction,
            "files": dict(sample.files),
            "verifier_code": sample.verifier_code,
            "capability_weights": dict(sample.capability_weights),
        }
        for sample in samples
    ]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
