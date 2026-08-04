"""把 Benchmark 原始分数归一化并聚合为固定六维能力画像。"""

from datetime import UTC, datetime

from evalhub.benchmarks.models import (
    BenchmarkSpec,
    BenchmarkSuiteSpec,
    Capability,
    NormalizationKind,
)
from evalhub.benchmarks.registry import get_benchmark_spec

CAPABILITY_LABELS = {
    Capability.KNOWLEDGE: "知识",
    Capability.INSTRUCTION_FOLLOWING: "指令遵循",
    Capability.MATHEMATICS: "数学",
    Capability.REASONING: "综合推理",
    Capability.CODING: "代码",
    Capability.SAFETY_TRUST: "安全可信",
}


def normalize_score(
    raw_score: float,
    normalization: NormalizationKind,
    random_baseline: float | None = None,
) -> float:
    """按照声明的协议把零到一原始指标转换为百分制。"""
    score = min(1.0, max(0.0, float(raw_score)))
    if normalization == NormalizationKind.SCALE_100:
        return round(score * 100, 4)
    if random_baseline is None or not 0 <= random_baseline < 1:
        raise ValueError("chance-corrected normalization requires a baseline in [0, 1)")
    corrected = max(0.0, (score - random_baseline) / (1 - random_baseline))
    return round(corrected * 100, 4)


def aggregate_capability_profile(
    suite: BenchmarkSuiteSpec,
    benchmark_outputs: list[dict[str, object]],
    *,
    benchmark_specs: tuple[BenchmarkSpec, ...] | None = None,
) -> dict[str, object]:
    """聚合成功结果并为失败或缺失能力保留未评测语义。

    Args:
        suite: 本次评测冻结的 Suite 身份、版本和成员顺序。
        benchmark_outputs: 各成员成功结果或类型化失败摘要。
        benchmark_specs: 创建任务时冻结的成员规格；缺省时兼容直接调用方查询当前 Registry。

    Returns:
        含六维得分、覆盖率和各 Benchmark 诊断行的能力画像。
    """
    supplied = {
        str(item["benchmark_id"]): item for item in benchmark_outputs if "benchmark_id" in item
    }
    frozen_specs = benchmark_specs or tuple(
        get_benchmark_spec(item) for item in suite.benchmark_ids
    )
    capabilities: dict[str, object] = {}

    for capability in Capability:
        specs = [spec for spec in frozen_specs if spec.capability == capability]
        total_weight = sum(item.weight for item in specs)
        successful_weight = 0.0
        weighted_score = 0.0
        rows: list[dict[str, object]] = []

        for spec in specs:
            output = supplied.get(spec.id, {})
            status = str(output.get("status", "unassessed"))
            row = {"benchmark_id": spec.id, "display_name": spec.display_name, "status": status}
            if status == "success" and output.get("raw_score") is not None:
                normalized = normalize_score(
                    float(output["raw_score"]), spec.normalization, spec.random_baseline
                )
                successful_weight += spec.weight
                weighted_score += normalized * spec.weight
                row.update({"raw_score": output["raw_score"], "normalized_score": normalized})
            elif output.get("error_type") is not None:
                row["error_type"] = output["error_type"]
            rows.append(row)

        coverage = successful_weight / total_weight if total_weight else 0.0
        if successful_weight == 0:
            score: float | None = None
            status = "unassessed"
        else:
            score = round(weighted_score / successful_weight, 4)
            status = "complete" if successful_weight == total_weight else "partial"
        capabilities[capability.value] = {
            "label": CAPABILITY_LABELS[capability],
            "score": score,
            "status": status,
            "coverage": round(coverage, 4),
            "benchmark_results": rows,
        }

    counts = {
        status: sum(1 for item in benchmark_outputs if item.get("status") == status)
        for status in ("success", "failed", "blocked")
    }
    assessed_dimensions = sum(1 for item in capabilities.values() if item["score"] is not None)
    overall_status = (
        "complete"
        if assessed_dimensions == len(Capability)
        else "partial"
        if assessed_dimensions > 0
        else "unassessed"
    )
    model = next((item.get("model") for item in benchmark_outputs if item.get("model")), None)
    return {
        "suite_id": suite.id,
        "suite_version": suite.version,
        "model": model,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": overall_status,
        "counts": counts,
        "capabilities": capabilities,
    }
