from evalhub.benchmarks import Capability, benchmark_registry, suite_registry


def test_industry_core_suite_covers_all_six_capabilities() -> None:
    benchmarks = benchmark_registry()
    suite = suite_registry()["llm-industry-core-v1"]

    assert {benchmarks[item].capability for item in suite.benchmark_ids} == set(Capability)
    assert len(suite.benchmark_ids) >= 13


def test_every_benchmark_has_reproducibility_metadata() -> None:
    for spec in benchmark_registry().values():
        assert spec.version
        assert spec.dataset_source
        assert spec.dataset_revision
        assert spec.metric
        assert spec.weight > 0
