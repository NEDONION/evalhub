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
from evalhub.benchmarks.humaneval import humaneval_verifier_identity
from evalhub.datasets import PinnedSource, dataset_catalog, hexagon_source_specs
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
    hexagon_ids = tuple(spec.id for spec in specs if spec.id.startswith("hexagon-"))
    manifest_sha256 = _hexagon_manifest_sha256() if hexagon_ids else None
    datasets = dataset_catalog()
    # 由成员而非 Suite ID 识别 Hexagon，使单项与完整套件共享同一固定来源边界。
    pinned_sources = hexagon_source_specs() if hexagon_ids else {}
    verifier_identity = (
        humaneval_verifier_identity() if "hexagon-humaneval" in hexagon_ids else None
    )
    source_contracts: dict[str, dict[str, str]] = {}
    for benchmark_id in hexagon_ids:
        source = pinned_sources.get(benchmark_id)
        if source is None:
            raise ValueError(f"missing pinned source contract: {benchmark_id}")
        source_contracts[benchmark_id] = _source_contract(source, benchmark_id)
    benchmark_protocols = [
        _benchmark_protocol(
            spec,
            datasets[spec.id].evaluator_type if spec.id in datasets else spec.metric,
            source_contracts.get(spec.id),
            verifier_identity if spec.id == "hexagon-humaneval" else None,
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
    # 最终结果直接发布创建时冻结合同，不能在终结阶段重新读取当前部署固定来源。
    if source_contracts:
        reproducibility["source_contracts"] = source_contracts
    if verifier_identity is not None:
        reproducibility["humaneval_verifier_identity"] = verifier_identity
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


def _benchmark_protocol(
    spec: BenchmarkSpec,
    evaluator_type: str,
    source_contract: dict[str, str] | None = None,
    verifier_identity: str | None = None,
) -> dict[str, object]:
    """把 Registry 规格冻结为后续运行无需重新查询的 JSON 协议事实。

    Args:
        spec: 创建任务时读取的一条不可变 Benchmark 规格。
        evaluator_type: 创建时数据集目录为该 Benchmark 选择的评分器类型。
        source_contract: Hexagon 在创建时读取的 Task 2 固定下载合同；其他套件为空。
        verifier_identity: HumanEval 固定镜像三文件执行上下文身份；其他成员为空。

    Returns:
        覆盖执行、评分、归一化、来源 revision 和固定下载合同的完整 JSON 映射。
    """
    protocol: dict[str, object] = {
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
    # 仅 Hexagon 增加该字段，避免改变非 Hexagon 工作流的持久化兼容形状。
    if source_contract is not None:
        protocol["source_contract"] = dict(source_contract)
    if verifier_identity is not None:
        protocol["verifier_identity"] = verifier_identity
    return protocol


def _source_contract(source: PinnedSource, benchmark_id: str) -> dict[str, str]:
    """把 Task 2 固定来源收窄为准备安全所需的不可变协议事实。

    Args:
        source: 当前任务创建部署中的一条固定来源记录。
        benchmark_id: 当前工作流成员要求匹配的稳定 Benchmark ID。

    Returns:
        只含稳定来源 ID、下载 URL、revision 和期望 SHA-256 的 JSON 映射。

    Raises:
        ValueError: 来源 ID 不匹配、必要字符串为空或 SHA-256 不是小写 64 位十六进制。
    """
    contract = {
        "source_id": source.benchmark_id,
        "url": source.url,
        "revision": source.revision,
        "sha256": source.sha256,
    }
    required_strings = (contract["url"], contract["revision"])
    valid_sha256 = len(source.sha256) == 64 and all(
        character in "0123456789abcdef" for character in source.sha256
    )
    # 创建端 fail-closed，避免把无法由运行时安全验证的半成品合同写入 SQLite。
    if source.benchmark_id != benchmark_id or not all(required_strings) or not valid_sha256:
        raise ValueError(f"invalid pinned source contract: {benchmark_id}")
    return contract


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
