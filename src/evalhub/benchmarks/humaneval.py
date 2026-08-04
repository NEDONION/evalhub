"""只通过固定 Docker 镜像执行 Hexagon HumanEval Pass@1 评分。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from evalhub.adapters.base import ModelAdapter

if TYPE_CHECKING:
    from evalhub.datasets.hexagon_manifest import HexagonSampleSpec

ProgressCallback = Callable[[int, int], None]
SampleDictCallback = Callable[[dict[str, object], int, int], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_IMAGE = "evalhub-humaneval:1.0.0"
_MAX_PAYLOAD_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 1024
_VERIFIER_REASONS = frozenset(
    {"invalid_payload", "timeout", "execution_failed", "verification_failed"}
)
_PUBLIC_FAILURE_REASONS = _VERIFIER_REASONS | frozenset(
    {"executor_not_ready", "sandbox_failed", "invalid_result"}
)


@dataclass(frozen=True)
class HumanEvalProblem:
    """保存一条选中题目的模型提示和只允许发送给 Docker 的官方校验字段。"""

    sample_id: str
    source_key: str
    prompt: str
    canonical_solution: str
    test: str
    entry_point: str
    input_zh: str


@dataclass(frozen=True)
class SandboxResult:
    """保存 Docker 验证器唯一允许返回的通过状态和固定短原因。"""

    passed: bool
    reason: str | None = None


class HumanEvalSandbox(Protocol):
    """描述 HumanEval Runner 依赖的最小隔离执行接口。"""

    def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
        """在隔离边界验证一次候选，并返回不含源码的安全结果。"""


class DockerHumanEvalSandbox:
    """通过无网络、无宿主挂载的固定 Docker 镜像验证一个 HumanEval 候选。"""

    image = _IMAGE

    def __init__(self, *, command_runner: CommandRunner = subprocess.run) -> None:
        """注入宿主命令边界，生产环境缺省使用 ``subprocess.run``。

        Args:
            command_runner: 接收固定 argv、stdin 和超时参数的文本命令执行器。
        """
        self._command_runner = command_runner

    def command(self) -> list[str]:
        """返回固定 Docker argv，明确禁网、只读、降权且限制资源。

        Returns:
            不含 shell、宿主挂载或动态镜像名的 Docker 参数列表。
        """
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=256m",
            "--cpus=1",
            "--pids-limit=64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            self.image,
        ]

    def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
        """把单题执行载荷写入固定容器 stdin，并把所有宿主故障收敛为安全失败。

        Args:
            problem: 包含英文提示、隐藏测试和入口点的选中官方题目。
            completion: 模型针对该提示生成的唯一候选补全。

        Returns:
            通过状态，或不包含进程输出、源码和异常细节的固定失败原因。
        """
        try:
            payload = _sandbox_payload(problem, completion)
        except ValueError:
            return SandboxResult(False, "invalid_payload")

        # Docker CLI 使用硬超时并捕获输出；不经过 shell，也不继承任何动态容器参数。
        try:
            completed = self._command_runner(
                self.command(),
                input=payload,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(False, "timeout")
        except UnicodeError:
            return SandboxResult(False, "invalid_result")
        except (FileNotFoundError, OSError):
            return SandboxResult(False, "executor_not_ready")
        if completed.returncode != 0:
            return SandboxResult(False, "sandbox_failed")
        return _parse_sandbox_result(completed.stdout)


def load_humaneval_problems(
    path: Path,
    *,
    manifest: tuple[HexagonSampleSpec, ...] | None = None,
) -> list[HumanEvalProblem]:
    """按冻结清单顺序从 gzip 加载十条 HumanEval 问题，并复核英文摘要。

    Args:
        path: 已通过固定来源摘要校验的 HumanEval gzip JSONL 文件。
        manifest: 测试可注入的清单；缺省使用随包发布的完整 Hexagon v1 清单。

    Returns:
        只包含清单选中 ID 的问题列表；不会在磁盘产生解压文件。

    Raises:
        ValueError: 选择器缺失、重复，或英文提示与标准实现偏离冻结摘要时抛出。
        OSError: gzip 文件不可读时保留底层文件系统错误。
    """
    # 数据集模块本身依赖 Benchmark 模型；延迟导入避免包级公开导出形成循环初始化。
    from evalhub.datasets.hexagon_manifest import hexagon_manifest
    from evalhub.datasets.hexagon_sources import load_selected_humaneval_rows

    frozen = manifest if manifest is not None else hexagon_manifest()
    selected = [item for item in frozen if item.benchmark_id == "hexagon-humaneval"]
    keys = [item.source_key for item in selected]
    rows = load_selected_humaneval_rows(path, keys)

    # 清单顺序是固定评测协议的一部分，来源文件的原始排列不能改变模型调用顺序。
    problems: list[HumanEvalProblem] = []
    for item in selected:
        row = rows[item.source_key]
        if hashlib.sha256(row.prompt.encode("utf-8")).hexdigest() != item.input_sha256:
            raise ValueError(f"input SHA-256 mismatch for {item.source_key}")
        if (
            hashlib.sha256(row.canonical_solution.encode("utf-8")).hexdigest()
            != item.reference_sha256
        ):
            raise ValueError(f"reference SHA-256 mismatch for {item.source_key}")
        # 标准实现与隐藏测试只进入专用问题对象，结果字典永远不序列化该对象。
        problems.append(
            HumanEvalProblem(
                sample_id=item.id,
                source_key=item.source_key,
                prompt=row.prompt,
                canonical_solution=row.canonical_solution,
                test=row.test,
                entry_point=row.entry_point,
                input_zh=item.input_zh,
            )
        )
    return problems


def run_humaneval_benchmark(
    *,
    job_id: str,
    adapter: ModelAdapter,
    problems: list[HumanEvalProblem],
    sandbox: HumanEvalSandbox,
    skip_sample_ids: frozenset[str] = frozenset(),
    on_progress: ProgressCallback | None = None,
    on_sample_result: SampleDictCallback | None = None,
) -> dict[str, object]:
    """为每题生成一次代码并在 Docker 中评分，返回 Pass@1 兼容摘要。

    Args:
        job_id: 调度层创建的稳定任务标识。
        adapter: 只接收官方英文提示的模型生成边界。
        problems: 按冻结清单顺序加载的 HumanEval 问题。
        sandbox: 唯一获准执行候选和隐藏测试的 Docker 边界。
        skip_sample_ids: 恢复执行时已经持久化、无需再次生成的样本标识。
        on_progress: 接收已完成数量和固定总题数的可选回调。
        on_sample_result: 每条新样本判定完成后接收脱敏结果的可选回调。

    Returns:
        包含 Pass@1 汇总和脱敏样本结果的 JSON 兼容字典。

    Raises:
        ValueError: 问题样本 ID 重复，无法安全支持断点恢复时抛出。
    """
    sample_ids = [problem.sample_id for problem in problems]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate HumanEval sample IDs")
    completed = sum(sample_id in skip_sample_ids for sample_id in sample_ids)
    if on_progress is not None:
        on_progress(completed, len(problems))
    sample_results: list[dict[str, object]] = []

    # 每题只生成一个确定性候选；中文翻译和官方答案都不会进入模型调用参数。
    for problem in problems:
        if problem.sample_id in skip_sample_ids:
            continue
        completion = adapter.generate(problem.prompt, temperature=0, num_predict=256)
        verdict = sandbox.run(problem, completion)
        sample_result = _sample_result(problem, completion, verdict)
        sample_results.append(sample_result)
        completed += 1
        if on_sample_result is not None:
            on_sample_result(sample_result, completed, len(problems))
        if on_progress is not None:
            on_progress(completed, len(problems))

    passed_samples = sum(float(item["score"]) >= 1.0 for item in sample_results)
    failed_ids = [
        str(item["sample_id"]) for item in sample_results if float(item["score"]) < 1.0
    ]
    # 汇总只引用脱敏样本字典，标准实现和隐藏测试没有任何可序列化入口。
    return {
        "job_id": job_id,
        "status": "success",
        "dataset": "hexagon-humaneval",
        "benchmark": "Hexagon · HumanEval",
        "metric": "pass@1",
        "total_samples": len(problems),
        "passed_samples": passed_samples,
        "average_score": round(passed_samples / len(sample_results), 4)
        if sample_results
        else 0.0,
        "failed_sample_ids": failed_ids,
        "failed_examples": [item for item in sample_results if float(item["score"]) < 1.0][:5],
        "sample_results": sample_results,
    }


def _sandbox_payload(problem: HumanEvalProblem, completion: str) -> str:
    """序列化固定四字段容器输入，并拒绝会放大管道内存的超长载荷。

    Args:
        problem: 包含提示、隐藏测试和入口点的官方问题。
        completion: 模型生成的唯一候选补全。

    Returns:
        紧凑且以换行结尾的 JSON 文本。

    Raises:
        ValueError: 载荷超过固定一 MiB 上限时抛出。
    """
    payload = json.dumps(
        {
            "prompt": problem.prompt,
            "completion": completion,
            "test": problem.test,
            "entry_point": problem.entry_point,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("HumanEval sandbox payload is too large")
    return f"{payload}\n"


def _parse_sandbox_result(output: str) -> SandboxResult:
    """严格解析有界验证器输出，拒绝额外字段、未知原因和非布尔状态。

    Args:
        output: Docker 进程捕获的完整标准输出。

    Returns:
        合法最小 JSON 对象对应的判定；其他任意输出统一返回安全失败。
    """
    if not isinstance(output, str):
        return SandboxResult(False, "invalid_result")
    try:
        if len(output.encode("utf-8")) > _MAX_RESULT_BYTES:
            return SandboxResult(False, "invalid_result")
    except UnicodeError:
        return SandboxResult(False, "invalid_result")
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, UnicodeError):
        return SandboxResult(False, "invalid_result")
    if not isinstance(payload, dict) or type(payload.get("passed")) is not bool:
        return SandboxResult(False, "invalid_result")

    # 通过对象只能有一个字段；失败对象只能携带白名单短原因，禁止任意诊断回流宿主。
    if payload["passed"] is True:
        return SandboxResult(True) if set(payload) == {"passed"} else SandboxResult(
            False, "invalid_result"
        )
    reason = payload.get("reason")
    if (
        set(payload) != {"passed", "reason"}
        or not isinstance(reason, str)
        or reason not in _VERIFIER_REASONS
    ):
        return SandboxResult(False, "invalid_result")
    return SandboxResult(False, reason)


def _sample_result(
    problem: HumanEvalProblem, completion: str, verdict: SandboxResult
) -> dict[str, object]:
    """把隔离判定转换为可持久化结果，并用固定参考文案替代隐藏测试。

    Args:
        problem: 当前题目的公开提示、来源键和中文展示翻译。
        completion: 模型生成且允许展示的一次候选补全。
        verdict: Docker 返回的脱敏通过状态。

    Returns:
        不包含 ``test`` 或 ``canonical_solution`` 的 JSON 兼容样本结果。
    """
    reason = verdict.reason if verdict.reason in _PUBLIC_FAILURE_REASONS else "sandbox_failed"
    return {
        "sample_id": problem.sample_id,
        "input": problem.prompt,
        "prediction": completion,
        "reference": "hidden tests passed",
        "metric": "pass@1",
        "score": 1.0 if verdict.passed else 0.0,
        "reason": None if verdict.passed else reason,
        "metadata": {"source_key": problem.source_key, "input_zh": problem.input_zh},
    }
