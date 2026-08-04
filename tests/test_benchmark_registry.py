"""验证行业 Benchmark 与 Suite Registry 的版本、覆盖和不可变性。"""

import pytest

from evalhub.benchmarks import (
    Capability,
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
    assert get_benchmark_spec("musr").task_name == "leaderboard_musr"
    assert get_benchmark_spec("musr").metric == "acc_norm"


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
