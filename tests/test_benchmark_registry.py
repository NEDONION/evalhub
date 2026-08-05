"""验证行业 Benchmark 与 Suite Registry 的版本、覆盖和不可变性。"""

import pytest

from evalhub.benchmarks import (
    Capability,
    NormalizationKind,
    benchmark_registry,
    get_benchmark_spec,
    get_suite_spec,
)


def test_core_suite_has_six_capabilities_and_real_sources() -> None:
    """核心套件必须覆盖固定六维能力并声明可追溯的真实上游来源。"""
    suite = get_suite_spec("llm-industry-core-v1")
    specs = [get_benchmark_spec(item) for item in suite.benchmark_ids]

    assert {item.capability for item in specs} == set(Capability)
    assert all(item.dataset_source and item.dataset_revision for item in specs)
    assert all(item.homepage.startswith("https://") for item in specs)
    assert all(item.weight > 0 for item in specs)


def test_core_suite_registers_all_thirteen_official_tasks() -> None:
    """核心套件必须固定包含 13 项，并使用当前 Harness 的 MATH-500 任务名。"""
    suite = get_suite_spec("llm-industry-core-v1")

    assert len(suite.benchmark_ids) == 13
    assert get_benchmark_spec("math-500").task_name == "hendrycks_math500"
    assert get_benchmark_spec("mmlu-pro").metric == "exact_match"
    assert get_benchmark_spec("arc-challenge").metric == "acc_norm"
    assert get_benchmark_spec("musr").task_name == "leaderboard_musr"
    assert get_benchmark_spec("musr").metric == "acc_norm"
    assert get_benchmark_spec("hellaswag").metric == "acc_norm"


def test_hexagon_suite_has_fixed_source_counts_and_six_dimensions() -> None:
    """Hexagon 套件必须固定七个来源切片，并完整覆盖六维能力。"""
    suite = get_suite_spec("evalhub-hexagon-v1")
    specs = [get_benchmark_spec(item) for item in suite.benchmark_ids]

    assert [item.expected_sample_count for item in specs] == [5, 5, 5, 5, 5, 3, 2]
    assert suite.version == "1.2.0"
    assert all(item.version == "1.2.0" for item in specs)
    assert {item.capability for item in specs} == set(Capability)
    assert all(item.normalization == NormalizationKind.SCALE_100 for item in specs)
    safety_specs = [item for item in specs if item.capability == Capability.SAFETY_TRUST]
    assert [item.weight for item in safety_specs] == [0.6, 0.4]


def test_hexagon_answer_protocols_freeze_per_benchmark_generation_budgets() -> None:
    """七项回答协议必须固定各自预算和版本，不能继续共享 256 token 默认值。"""
    suite = get_suite_spec("evalhub-hexagon-v1")
    specs = [get_benchmark_spec(item) for item in suite.benchmark_ids]

    assert [item.generation_config["num_predict"] for item in specs] == [
        256,
        1024,
        512,
        512,
        1024,
        256,
        256,
    ]
    assert [item.answer_protocol_version for item in specs] == [
        "choice-letter-v1",
        "ifeval-strict-v1",
        "numeric-exact-v1",
        "bbh-answer-v1",
        "humaneval-code-v2",
        "choice-letter-v1",
        "choice-letter-v1",
    ]
    assert get_benchmark_spec("hexagon-gsm8k").metric == "numeric_exact_match"
    assert get_benchmark_spec("hexagon-bbh").metric == "bbh_answer"


def test_registry_returns_new_mappings_with_stable_members() -> None:
    """调用方修改返回映射时不得污染后续 Registry 查询。"""
    first = benchmark_registry()
    first.pop("gsm8k")

    assert "gsm8k" in benchmark_registry()
    assert get_benchmark_spec("gsm8k").version == "1.0.0"


def test_generation_config_is_immutable() -> None:
    """冻结规格中的生成配置也必须拒绝原地修改。"""
    spec = get_benchmark_spec("gsm8k")

    with pytest.raises(TypeError):
        spec.generation_config["temperature"] = 1  # type: ignore[index]


def test_unknown_registry_ids_return_actionable_errors() -> None:
    """未知 ID 的错误必须包含可用候选，便于 API 和日志诊断。"""
    with pytest.raises(KeyError, match="unknown benchmark: missing; available:"):
        get_benchmark_spec("missing")
