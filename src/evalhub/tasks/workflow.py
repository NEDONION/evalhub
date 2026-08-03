"""根据单项 Benchmark 或版本化 Suite 生成固定评测工作流。"""

from evalhub.benchmarks import (
    BenchmarkSuiteSpec,
    get_benchmark_spec,
    get_suite_spec,
)
from evalhub.tasks.models import TaskRequest, WorkflowNodeSpec


def workflow_suite(request: TaskRequest) -> BenchmarkSuiteSpec:
    """返回请求使用的真实 Suite 或单 Benchmark 临时 Suite。"""
    if request.suite_id:
        return get_suite_spec(request.suite_id)
    benchmark = get_benchmark_spec(request.dataset)
    return BenchmarkSuiteSpec(
        id=f"single-benchmark:{benchmark.id}",
        version=benchmark.version,
        display_name=f"单项评测 · {benchmark.display_name}",
        benchmark_ids=(benchmark.id,),
    )


def build_workflow(request: TaskRequest) -> tuple[WorkflowNodeSpec, ...]:
    """按 Registry 稳定顺序构建准备、评测、聚合和终结节点。"""
    suite = workflow_suite(request)
    benchmark_keys = tuple(f"benchmark:{item}" for item in suite.benchmark_ids)
    nodes: list[WorkflowNodeSpec] = [
        WorkflowNodeSpec(
            node_key="prepare_assets",
            kind="prepare_assets",
            input={"suite_id": suite.id, "benchmark_ids": list(suite.benchmark_ids)},
        )
    ]
    for benchmark_id, node_key in zip(suite.benchmark_ids, benchmark_keys, strict=True):
        spec = get_benchmark_spec(benchmark_id)
        nodes.append(
            WorkflowNodeSpec(
                node_key=node_key,
                kind="benchmark",
                depends_on=("prepare_assets",),
                input={
                    "benchmark_id": spec.id,
                    "benchmark_version": spec.version,
                    "capability": spec.capability.value,
                    "dataset_source": spec.dataset_source,
                    "dataset_revision": spec.dataset_revision,
                    "executor": spec.executor.value,
                    "task_name": spec.task_name,
                    "metric": spec.metric,
                    "generation_config": dict(spec.generation_config),
                    "model": request.model,
                    "adapter": request.adapter,
                    "sample_mode": request.sample_mode,
                    "subject": request.subject,
                    "limit": request.limit,
                },
            )
        )
    nodes.extend(
        (
            WorkflowNodeSpec(
                node_key="capability_aggregate",
                kind="capability_aggregate",
                depends_on=benchmark_keys,
                input={"suite_id": suite.id, "suite_version": suite.version},
                max_attempts=1,
            ),
            WorkflowNodeSpec(
                node_key="workflow_finalize",
                kind="workflow_finalize",
                depends_on=("capability_aggregate",),
                input={"suite_id": suite.id},
                max_attempts=1,
            ),
        )
    )
    return tuple(nodes)
