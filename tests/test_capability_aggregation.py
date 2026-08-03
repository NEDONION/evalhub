"""验证原始 Benchmark 分数归一化和六维能力画像聚合。"""

from evalhub.benchmarks import (
    NormalizationKind,
    aggregate_capability_profile,
    get_suite_spec,
    normalize_score,
)


def test_chance_corrected_score_uses_random_baseline() -> None:
    """多项选择分数应扣除随机基线后再映射为百分制。"""
    assert normalize_score(0.625, NormalizationKind.CHANCE_CORRECTED, 0.25) == 50.0
    assert normalize_score(0.1, NormalizationKind.CHANCE_CORRECTED, 0.25) == 0.0


def test_scale_100_normalization_clamps_invalid_boundaries() -> None:
    """普通零到一指标应转换为百分制并限制在合法范围。"""
    assert normalize_score(0.8, NormalizationKind.SCALE_100) == 80.0
    assert normalize_score(1.2, NormalizationKind.SCALE_100) == 100.0
    assert normalize_score(-0.1, NormalizationKind.SCALE_100) == 0.0


def test_partial_profile_keeps_missing_axes_unassessed() -> None:
    """只有 GSM8K 成功时数学可评分，其他能力不能伪造为零分。"""
    suite = get_suite_spec("llm-industry-core-v1")
    profile = aggregate_capability_profile(
        suite,
        [
            {
                "benchmark_id": "gsm8k",
                "status": "success",
                "raw_score": 0.8,
                "model": "oracle",
            },
            {
                "benchmark_id": "math-500",
                "status": "blocked",
                "error_type": "executor_not_ready",
            },
        ],
    )

    mathematics = profile["capabilities"]["mathematics"]
    knowledge = profile["capabilities"]["knowledge"]
    assert profile["status"] == "partial"
    assert mathematics["score"] == 80.0
    assert mathematics["status"] == "partial"
    assert 0 < mathematics["coverage"] < 1
    assert knowledge["score"] is None
    assert knowledge["status"] == "unassessed"
    assert knowledge["coverage"] == 0.0


def test_profile_reports_unassessed_when_no_benchmark_succeeds() -> None:
    """没有成功 Benchmark 时画像应整体未评测并保留六个空能力轴。"""
    profile = aggregate_capability_profile(
        get_suite_spec("llm-industry-core-v1"),
        [{"benchmark_id": "gsm8k", "status": "failed", "error_type": "timeout"}],
    )

    assert profile["status"] == "unassessed"
    assert profile["counts"] == {"success": 0, "failed": 1, "blocked": 0}
    assert len(profile["capabilities"]) == 6
    assert all(item["score"] is None for item in profile["capabilities"].values())
