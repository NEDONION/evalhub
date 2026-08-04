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
        "acc",
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
        "leaderboard_math_hard",
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
        "acc",
        0.25,
    ),
    (
        "musr",
        "MuSR",
        Capability.REASONING,
        "TAUR-Lab/MuSR",
        ExecutorKind.LM_EVAL,
        "musr",
        "acc",
        0.5,
    ),
    (
        "hellaswag",
        "HellaSwag",
        Capability.REASONING,
        "Rowan/hellaswag",
        ExecutorKind.LM_EVAL,
        "hellaswag",
        "acc",
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

HEXAGON_ROWS: tuple[BenchmarkRow, ...] = (
    (
        "hexagon-mmlu",
        "Hexagon · MMLU",
        Capability.KNOWLEDGE,
        "hendrycks/test",
        ExecutorKind.NATIVE,
        "hexagon_mmlu",
        "acc",
        None,
    ),
    (
        "hexagon-ifeval",
        "Hexagon · IFEval",
        Capability.INSTRUCTION_FOLLOWING,
        "google/IFEval",
        ExecutorKind.NATIVE,
        "hexagon_ifeval",
        "prompt_level_strict_acc",
        None,
    ),
    (
        "hexagon-gsm8k",
        "Hexagon · GSM8K",
        Capability.MATHEMATICS,
        "openai/grade-school-math",
        ExecutorKind.NATIVE,
        "hexagon_gsm8k",
        "exact_match",
        None,
    ),
    (
        "hexagon-bbh",
        "Hexagon · BBH",
        Capability.REASONING,
        "suzgunmirac/BIG-Bench-Hard",
        ExecutorKind.NATIVE,
        "hexagon_bbh",
        "exact_match",
        None,
    ),
    (
        "hexagon-humaneval",
        "Hexagon · HumanEval",
        Capability.CODING,
        "openai/human-eval",
        ExecutorKind.SANDBOXED_CODE,
        "hexagon_humaneval",
        "pass@1",
        None,
    ),
    (
        "hexagon-truthfulqa",
        "Hexagon · TruthfulQA",
        Capability.SAFETY_TRUST,
        "sylinrl/TruthfulQA",
        ExecutorKind.NATIVE,
        "hexagon_truthfulqa",
        "acc",
        None,
    ),
    (
        "hexagon-bbq",
        "Hexagon · BBQ",
        Capability.SAFETY_TRUST,
        "nyu-mll/BBQ",
        ExecutorKind.NATIVE,
        "hexagon_bbq",
        "acc",
        None,
    ),
)

DATASET_REVISION = "resolved-at-runtime:sha256"
PROTOCOL_VERSION = "1.0.0"

_HEXAGON_SAMPLE_COUNTS = (10, 10, 10, 10, 10, 5, 5)
_HEXAGON_REVISIONS = {
    "hexagon-mmlu": "sha256:bec563ba4bac1d6aaf04141cd7d1605d7a5ca833e38f994051e818489592989b",
    "hexagon-ifeval": "8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0",
    "hexagon-gsm8k": "3101c7d5072418e28b9008a6636bde82a006892c",
    "hexagon-bbh": "9ee07bd481feebf959a6b59d61ea57bdcf30964d",
    "hexagon-humaneval": "6d43fb980f9fee3c892a914eda09951f772ad10d",
    "hexagon-truthfulqa": "d71c110897f5d31c5d7f309e7bc316c152f6f031",
    "hexagon-bbq": "bea11bd97d79217245b5871acd247b9d6eb24598",
}
_HEXAGON_LICENSES = {
    "hexagon-mmlu": "MIT",
    "hexagon-ifeval": "CC-BY-4.0",
    "hexagon-gsm8k": "MIT",
    "hexagon-bbh": "MIT",
    "hexagon-humaneval": "MIT",
    "hexagon-truthfulqa": "Apache-2.0",
    "hexagon-bbq": "CC-BY-4.0",
}

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
    "hexagon-mmlu": "https://github.com/hendrycks/test",
    "hexagon-ifeval": "https://github.com/google-research/google-research/tree/master/instruction_following_eval",
    "hexagon-gsm8k": "https://github.com/openai/grade-school-math",
    "hexagon-bbh": "https://github.com/suzgunmirac/BIG-Bench-Hard",
    "hexagon-humaneval": "https://github.com/openai/human-eval",
    "hexagon-truthfulqa": "https://github.com/sylinrl/TruthfulQA",
    "hexagon-bbq": "https://github.com/nyu-mll/BBQ",
}


def _build_benchmark_registry() -> dict[str, BenchmarkSpec]:
    """构建核心与 Hexagon Benchmark 映射，并拒绝重复 ID 或无效权重。

    Returns:
        以稳定 Benchmark ID 为键的新规格映射，包含向后兼容的核心套件及固定 Hexagon 来源。

    Raises:
        ValueError: 任一 Benchmark ID 重复，或生成的评测权重不为正数。
    """
    registry: dict[str, BenchmarkSpec] = {}
    for index, row in enumerate((*CORE_ROWS, *HEXAGON_ROWS)):
        benchmark_id, display_name, capability, source, executor, task_name, metric, baseline = row
        if benchmark_id in registry:
            raise ValueError(f"duplicate benchmark id: {benchmark_id}")
        # Hexagon 使用固定来源资产；原有 Core Suite 仍保持运行时解析来源版本的兼容行为。
        is_hexagon = benchmark_id.startswith("hexagon-")
        sample_count = _HEXAGON_SAMPLE_COUNTS[index - len(CORE_ROWS)] if is_hexagon else None
        spec = BenchmarkSpec(
            id=benchmark_id,
            version=PROTOCOL_VERSION,
            display_name=display_name,
            capability=capability,
            dataset_source=source,
            dataset_revision=_HEXAGON_REVISIONS[benchmark_id] if is_hexagon else DATASET_REVISION,
            homepage=_HOMEPAGES[benchmark_id],
            license=_HEXAGON_LICENSES[benchmark_id] if is_hexagon else "upstream",
            expected_sample_count=sample_count,
            executor=executor,
            task_name=task_name,
            metric=metric,
            normalization=(
                NormalizationKind.SCALE_100
                if is_hexagon or baseline is None
                else NormalizationKind.CHANCE_CORRECTED
            ),
            random_baseline=None if is_hexagon else baseline,
            weight=0.5 if is_hexagon and capability == Capability.SAFETY_TRUST else 1.0,
        )
        if spec.weight <= 0:
            raise ValueError(f"benchmark weight must be positive: {benchmark_id}")
        registry[benchmark_id] = spec
    return registry


def benchmark_registry() -> dict[str, BenchmarkSpec]:
    """返回当前协议版本的 Benchmark 规格映射。"""
    return _build_benchmark_registry()


def suite_registry() -> dict[str, BenchmarkSuiteSpec]:
    """返回核心套件及固定 60 题 Hexagon 套件的版本化规格映射。

    Returns:
        以套件稳定 ID 为键的新映射，成员顺序决定每个套件的固定执行顺序。

    Raises:
        ValueError: 套件引用未注册的 Benchmark 时，防止入口层暴露不可执行配置。
    """
    benchmarks = benchmark_registry()
    core_ids = tuple(row[0] for row in CORE_ROWS)
    hexagon_ids = tuple(row[0] for row in HEXAGON_ROWS)
    if any(item not in benchmarks for item in core_ids):
        raise ValueError("core suite contains unknown benchmark")
    if any(item not in benchmarks for item in hexagon_ids):
        raise ValueError("hexagon suite contains unknown benchmark")
    return {
        "llm-industry-core-v1": BenchmarkSuiteSpec(
            id="llm-industry-core-v1",
            version=PROTOCOL_VERSION,
            display_name="LLM 行业核心套件 v1",
            benchmark_ids=core_ids,
        ),
        "evalhub-hexagon-v1": BenchmarkSuiteSpec(
            id="evalhub-hexagon-v1",
            version=PROTOCOL_VERSION,
            display_name="EvalHub 专业六边形套件 v1",
            benchmark_ids=hexagon_ids,
        ),
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
