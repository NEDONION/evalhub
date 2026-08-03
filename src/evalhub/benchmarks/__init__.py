"""公开版本化 Benchmark Registry、能力聚合和内置 Agent Benchmark 入口。"""

from evalhub.benchmarks.aggregation import aggregate_capability_profile, normalize_score
from evalhub.benchmarks.coding_mini import (
    CAPABILITY_DIMENSIONS,
    CodingAgentSample,
    coding_mini_samples,
    run_codex_agent_benchmark,
)
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
    "CAPABILITY_DIMENSIONS",
    "BenchmarkSpec",
    "BenchmarkSuiteSpec",
    "Capability",
    "CodingAgentSample",
    "ExecutorKind",
    "NormalizationKind",
    "aggregate_capability_profile",
    "benchmark_registry",
    "coding_mini_samples",
    "get_benchmark_spec",
    "get_suite_spec",
    "normalize_score",
    "run_codex_agent_benchmark",
    "suite_registry",
]
