"""根据单项 Benchmark 或版本化 Suite 生成固定评测工作流。"""

import hashlib
import json
from importlib.resources import files

from evalhub.benchmarks import (
    BenchmarkSpec,
    BenchmarkSuiteSpec,
    get_benchmark_spec,
    get_suite_spec,
)
from evalhub.datasets import dataset_catalog
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
    """按 Registry 稳定顺序冻结准备、评测、聚合和终结节点。

    Args:
        request: 已校验的单项或 Suite 模型评测请求。

    Returns:
        带固定题数、来源 revision、提示版本和生成配置的不可变节点规格元组。
    """
    suite = workflow_suite(request)
    specs = tuple(get_benchmark_spec(item) for item in suite.benchmark_ids)
    manifest_sha256 = (
        _hexagon_manifest_sha256() if suite.id == "evalhub-hexagon-v1" else None
    )
    datasets = dataset_catalog()
    benchmark_protocols = [
        _benchmark_protocol(
            spec,
            datasets[spec.id].evaluator_type if spec.id in datasets else spec.metric,
        )
        for spec in specs
    ]
    protocol_fingerprint = _protocol_fingerprint(suite, manifest_sha256, benchmark_protocols)
    reproducibility = {
        "suite_version": suite.version,
        "manifest_sha256": manifest_sha256,
        "source_revisions": {spec.id: spec.dataset_revision for spec in specs},
        "prompt_template_versions": {
            spec.id: spec.prompt_template_version for spec in specs
        },
        "generation_config": dict(specs[0].generation_config) if specs else {},
    }
    benchmark_keys = tuple(f"benchmark:{item}" for item in suite.benchmark_ids)
    nodes: list[WorkflowNodeSpec] = [
        WorkflowNodeSpec(
            node_key="prepare_assets",
            kind="prepare_assets",
            input={
                "suite_id": suite.id,
                "suite_version": suite.version,
                "benchmark_ids": list(suite.benchmark_ids),
                "protocol_fingerprint": protocol_fingerprint,
            },
        )
    ]
    for spec, node_key, protocol in zip(
        specs, benchmark_keys, benchmark_protocols, strict=True
    ):
        nodes.append(
            WorkflowNodeSpec(
                node_key=node_key,
                kind="benchmark",
                depends_on=("prepare_assets",),
                input={
                    **protocol,
                    "suite_id": suite.id,
                    "suite_version": suite.version,
                    "manifest_sha256": manifest_sha256,
                    "protocol_fingerprint": protocol_fingerprint,
                    "model": request.model,
                    "adapter": request.adapter,
                    "sample_mode": request.sample_mode,
                    "subject": (
                        "all"
                        if request.suite_id is not None and spec.id == "mmlu"
                        else request.subject
                    ),
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
                input={
                    "suite_id": suite.id,
                    "suite_version": suite.version,
                    "suite_display_name": suite.display_name,
                    "benchmark_ids": list(suite.benchmark_ids),
                    "protocol_fingerprint": protocol_fingerprint,
                },
                max_attempts=1,
            ),
            WorkflowNodeSpec(
                node_key="workflow_finalize",
                kind="workflow_finalize",
                depends_on=("capability_aggregate",),
                input={
                    "suite_id": suite.id,
                    "suite_version": suite.version,
                    "suite_display_name": suite.display_name,
                    "reproducibility": reproducibility,
                    "protocol_fingerprint": protocol_fingerprint,
                },
                max_attempts=1,
            ),
        )
    )
    return tuple(nodes)


def _benchmark_protocol(spec: BenchmarkSpec, evaluator_type: str) -> dict[str, object]:
    """把 Registry 规格冻结为后续运行无需重新查询的 JSON 协议事实。

    Args:
        spec: 创建任务时读取的一条不可变 Benchmark 规格。
        evaluator_type: 创建时数据集目录为该 Benchmark 选择的评分器类型。

    Returns:
        覆盖执行、评分、归一化和来源 revision 的完整 JSON 映射。
    """
    return {
        "benchmark_id": spec.id,
        "benchmark_version": spec.version,
        "benchmark_display_name": spec.display_name,
        "capability": spec.capability.value,
        "dataset_source": spec.dataset_source,
        "dataset_revision": spec.dataset_revision,
        "homepage": spec.homepage,
        "license": spec.license,
        "expected_sample_count": spec.expected_sample_count,
        "executor": spec.executor.value,
        "task_name": spec.task_name,
        "metric": spec.metric,
        "evaluator_type": evaluator_type,
        "normalization": spec.normalization.value,
        "random_baseline": spec.random_baseline,
        "weight": spec.weight,
        "prompt_template_version": spec.prompt_template_version,
        "few_shot": spec.few_shot,
        "generation_config": dict(spec.generation_config),
        "requirements": list(spec.requirements),
    }


def _protocol_fingerprint(
    suite: BenchmarkSuiteSpec,
    manifest_sha256: str | None,
    benchmarks: list[dict[str, object]],
) -> str:
    """计算由工作流创建事实唯一决定的完整协议指纹。

    Args:
        suite: 创建时读取的 Suite ID、版本、名称和成员顺序。
        manifest_sha256: Hexagon 清单原始字节摘要；其他 Suite 为 ``None``。
        benchmarks: 按 Suite 顺序冻结的完整 Benchmark 协议映射。

    Returns:
        规范 JSON 字节对应的 SHA-256 十六进制摘要。
    """
    payload = {
        "suite": {
            "id": suite.id,
            "version": suite.version,
            "display_name": suite.display_name,
            "benchmark_ids": list(suite.benchmark_ids),
        },
        "manifest_sha256": manifest_sha256,
        "benchmarks": benchmarks,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hexagon_manifest_sha256() -> str:
    """读取创建任务时随包发布的 Hexagon v1 清单摘要。

    Returns:
        清单原始字节的 SHA-256，用于冻结选择集合 revision。
    """
    resource = files("evalhub.datasets").joinpath("manifests/hexagon_v1.json")
    return hashlib.sha256(resource.read_bytes()).hexdigest()


def build_agent_workflow(request: TaskRequest) -> tuple[WorkflowNodeSpec, ...]:
    """为固定 Agent 壳构建单个可审计 Benchmark 节点。

    Args:
        request: 已由 API 校验过的 Agent 评测请求。

    Returns:
        只包含一次 Agent Benchmark 执行的最小工作流，避免复用模型评测 DAG。

    Raises:
        ValueError: 请求不是 Agent 评测，或缺少固定 Agent 框架。
    """
    if request.evaluation_type != "agent":
        raise ValueError("agent workflow requires an agent evaluation request")
    if request.agent_framework is None:
        raise ValueError("agent workflow requires an agent framework")
    return (
        WorkflowNodeSpec(
            node_key=f"agent:{request.dataset}",
            kind="agent_benchmark",
            input={
                "benchmark_id": request.dataset,
                "agent_framework": request.agent_framework,
                "model": request.model,
                "adapter": request.adapter,
            },
            max_attempts=1,
        ),
    )
