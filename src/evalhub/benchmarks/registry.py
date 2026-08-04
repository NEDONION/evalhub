"""声明并校验 EvalHub 内置的版本化 Benchmark 与 Suite Registry。"""

from typing import TypeAlias

from evalhub.benchmarks.models import (
    BenchmarkSpec,
    BenchmarkSuiteSpec,
    Capability,
    ExecutorKind,
    NormalizationKind,
)

BenchmarkRow: TypeAlias = tuple[
    str,
    str,
    Capability,
    str,
    ExecutorKind,
    str,
    str,
    float | None,
]

CORE_ROWS: tuple[BenchmarkRow, ...] = (
    (
        "mmlu-pro",
        "MMLU-Pro",
        Capability.KNOWLEDGE,
        "TIGER-Lab/MMLU-Pro",
        ExecutorKind.LM_EVAL,
        "mmlu_pro",
        "exact_match",
        0.1,
    ),
    (
        "mmlu",
        "MMLU",
        Capability.KNOWLEDGE,
        "hendrycks/test",
        ExecutorKind.NATIVE,
        "mmlu",
        "acc",
        0.25,
    ),
    (
        "ifeval",
        "IFEval",
        Capability.INSTRUCTION_FOLLOWING,
        "google/IFEval",
        ExecutorKind.LM_EVAL,
        "ifeval",
        "prompt_level_strict_acc",
        None,
    ),
    (
        "gsm8k",
        "GSM8K",
        Capability.MATHEMATICS,
        "openai/grade-school-math",
        ExecutorKind.NATIVE,
        "gsm8k",
        "exact_match",
        None,
    ),
    (
        "math-500",
        "MATH-500",
        Capability.MATHEMATICS,
        "HuggingFaceH4/MATH-500",
        ExecutorKind.LM_EVAL,
        "hendrycks_math500",
        "exact_match",
        None,
    ),
    (
        "bbh",
        "BIG-Bench Hard",
        Capability.REASONING,
        "google/BIG-bench",
        ExecutorKind.LM_EVAL,
        "bbh",
        "exact_match",
        None,
    ),
    (
        "arc-challenge",
        "ARC-Challenge",
        Capability.REASONING,
        "allenai/ai2_arc",
        ExecutorKind.LM_EVAL,
        "arc_challenge",
        "acc_norm",
        0.25,
    ),
    (
        "musr",
        "MuSR",
        Capability.REASONING,
        "TAUR-Lab/MuSR",
        ExecutorKind.LM_EVAL,
        "leaderboard_musr",
        "acc_norm",
        0.5,
    ),
    (
        "hellaswag",
        "HellaSwag",
        Capability.REASONING,
        "Rowan/hellaswag",
        ExecutorKind.LM_EVAL,
        "hellaswag",
        "acc_norm",
        0.25,
    ),
    (
        "humaneval",
        "HumanEval",
        Capability.CODING,
        "openai/human-eval",
        ExecutorKind.SANDBOXED_CODE,
        "humaneval",
        "pass@1",
        None,
    ),
    (
        "mbpp",
        "MBPP",
        Capability.CODING,
        "google-research-datasets/mbpp",
        ExecutorKind.SANDBOXED_CODE,
        "mbpp",
        "pass@1",
        None,
    ),
    (
        "truthfulqa",
        "TruthfulQA",
        Capability.SAFETY_TRUST,
        "sylinrl/TruthfulQA",
        ExecutorKind.LM_EVAL,
        "truthfulqa_mc1",
        "acc",
        0.25,
    ),
    (
        "bbq",
        "BBQ",
        Capability.SAFETY_TRUST,
        "nyu-mll/BBQ",
        ExecutorKind.LM_EVAL,
        "bbq",
        "acc",
        1 / 3,
    ),
)

DATASET_REVISION = "resolved-at-runtime:sha256"
PROTOCOL_VERSION = "1.0.0"

_HOMEPAGES = {
    "mmlu-pro": "https://github.com/TIGER-AI-Lab/MMLU-Pro",
    "mmlu": "https://github.com/hendrycks/test",
    "ifeval": "https://github.com/google-research/google-research/tree/master/instruction_following_eval",
    "gsm8k": "https://github.com/openai/grade-school-math",
    "math-500": "https://huggingface.co/datasets/HuggingFaceH4/MATH-500",
    "bbh": "https://github.com/suzgu/BBH",
    "arc-challenge": "https://allenai.org/data/arc",
    "musr": "https://github.com/TIGER-AI-Lab/MuSR",
    "hellaswag": "https://github.com/rowanz/hellaswag",
    "humaneval": "https://github.com/openai/human-eval",
    "mbpp": "https://github.com/google-research/google-research/tree/master/mbpp",
    "truthfulqa": "https://github.com/sylinrl/TruthfulQA",
    "bbq": "https://github.com/nyu-mll/BBQ",
}


def _build_benchmark_registry() -> dict[str, BenchmarkSpec]:
    """构建 Benchmark 映射并拒绝重复 ID 或无效权重。"""
    registry: dict[str, BenchmarkSpec] = {}
    for row in CORE_ROWS:
        benchmark_id, display_name, capability, source, executor, task_name, metric, baseline = row
        if benchmark_id in registry:
            raise ValueError(f"duplicate benchmark id: {benchmark_id}")
        spec = BenchmarkSpec(
            id=benchmark_id,
            version=PROTOCOL_VERSION,
            display_name=display_name,
            capability=capability,
            dataset_source=source,
            dataset_revision=DATASET_REVISION,
            homepage=_HOMEPAGES[benchmark_id],
            license="upstream",
            expected_sample_count=None,
            executor=executor,
            task_name=task_name,
            metric=metric,
            normalization=(
                NormalizationKind.CHANCE_CORRECTED
                if baseline is not None
                else NormalizationKind.SCALE_100
            ),
            random_baseline=baseline,
        )
        if spec.weight <= 0:
            raise ValueError(f"benchmark weight must be positive: {benchmark_id}")
        registry[benchmark_id] = spec
    return registry


def benchmark_registry() -> dict[str, BenchmarkSpec]:
    """返回当前协议版本的 Benchmark 规格映射。"""
    return _build_benchmark_registry()


def suite_registry() -> dict[str, BenchmarkSuiteSpec]:
    """返回当前协议版本的 Suite 规格映射。"""
    benchmarks = benchmark_registry()
    benchmark_ids = tuple(row[0] for row in CORE_ROWS)
    if any(item not in benchmarks for item in benchmark_ids):
        raise ValueError("core suite contains unknown benchmark")
    return {
        "llm-industry-core-v1": BenchmarkSuiteSpec(
            id="llm-industry-core-v1",
            version=PROTOCOL_VERSION,
            display_name="LLM 行业核心套件 v1",
            benchmark_ids=benchmark_ids,
        )
    }


def get_benchmark_spec(benchmark_id: str) -> BenchmarkSpec:
    """按稳定 ID 获取 Benchmark 规格并在失败时列出候选。"""
    registry = benchmark_registry()
    try:
        return registry[benchmark_id]
    except KeyError as exc:
        available = ", ".join(sorted(registry))
        raise KeyError(f"unknown benchmark: {benchmark_id}; available: {available}") from exc


def get_suite_spec(suite_id: str) -> BenchmarkSuiteSpec:
    """按稳定 ID 获取 Suite 规格并在失败时列出候选。"""
    registry = suite_registry()
    try:
        return registry[suite_id]
    except KeyError as exc:
        available = ", ".join(sorted(registry))
        raise KeyError(f"unknown suite: {suite_id}; available: {available}") from exc
