"""公开版本化 Benchmark Registry 与能力画像聚合入口。"""

from evalhub.benchmarks.aggregation import aggregate_capability_profile, normalize_score
from evalhub.benchmarks.models import (
    BenchmarkSpec,
    BenchmarkSuiteSpec,
    Capability,
    ExecutorKind,
    NormalizationKind,
)
from evalhub.benchmarks.registry import (
    benchmark_registry,
    get_benchmark_spec,
    get_suite_spec,
    suite_registry,
)

__all__ = [
    "BenchmarkSpec",
    "BenchmarkSuiteSpec",
    "Capability",
    "ExecutorKind",
    "NormalizationKind",
    "aggregate_capability_profile",
    "benchmark_registry",
    "get_benchmark_spec",
    "get_suite_spec",
    "normalize_score",
    "suite_registry",
]
