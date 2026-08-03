"""公开 EvalHub 的版本化 Benchmark 与 Suite Registry 接口。"""

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
    "benchmark_registry",
    "get_benchmark_spec",
    "get_suite_spec",
    "suite_registry",
]
