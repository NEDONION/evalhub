"""执行由系统生成的持久化评测节点并维护断点、重试和能力画像。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from threading import Event

from evalhub.benchmarks import (
    BenchmarkSpec,
    BenchmarkSuiteSpec,
    Capability,
    ExecutorKind,
    ExecutorReadiness,
    NormalizationKind,
    aggregate_capability_profile,
    benchmark_readiness,
)
from evalhub.benchmarks.humaneval import humaneval_verifier_identity
from evalhub.datasets import hexagon_source_specs, prepare_dataset
from evalhub.tasks.executor import (
    SubprocessEvaluationExecutor,
    TaskExecutionCanceled,
    TaskExecutionError,
)
from evalhub.tasks.models import (
    EvaluationNode,
    EvaluationSampleCheckpoint,
    ResourceUsage,
    TaskRequest,
)
from evalhub.tasks.repository import SQLiteTaskRepository

TERMINAL_NODE_STATUSES = frozenset({"success", "failed", "blocked", "canceled"})


class WorkflowIncompleteError(RuntimeError):
    """表示流程已生成可审计产物，但一个或多个必需 Benchmark 未成功。"""


class RuntimeBlockedError(RuntimeError):
    """表示确定性环境、数据或配置问题需要人工处理。"""

    def __init__(self, error_type: str, message: str) -> None:
        """保存稳定错误分类和面向用户的安全消息。"""
        super().__init__(message)
        self.error_type = error_type


def classify_runtime_error(exc: Exception) -> str:
    """把执行异常收敛为自动重试、人工阻塞或普通失败。"""
    if isinstance(exc, RuntimeBlockedError):
        return "blocked"
    if isinstance(exc, TaskExecutionError):
        # 只有专用沙箱边界会携带稳定分类；这类环境故障不能写成模型失败分数。
        if exc.error_type is not None:
            return "blocked"
        message = str(exc).lower()
        transient_markers = (
            "timeout",
            "timed out",
            "connection",
            "429",
            " 500",
            " 502",
            " 503",
            " 504",
            "exited without result",
        )
        return "transient" if any(marker in message for marker in transient_markers) else "failed"
    if isinstance(exc, (KeyError, ValueError)):
        return "blocked"
    return "failed"


class PersistentWorkflowExecutor:
    """在现有单 Worker 内顺序执行并持久化一个评测工作流。"""

    def __init__(
        self,
        repository: SQLiteTaskRepository,
        *,
        benchmark_executor: object | None = None,
        asset_preparer: Callable[[str], object] = prepare_dataset,
        readiness_checker: Callable[[BenchmarkSpec], ExecutorReadiness] = benchmark_readiness,
    ) -> None:
        """注入持久化和执行边界，允许单元测试替换所有外部依赖。

        Args:
            repository: 保存节点、检查点、样本和最终结果的 SQLite 仓储。
            benchmark_executor: 运行单个 Benchmark 的可替换隔离执行器。
            asset_preparer: 校验或准备指定 Benchmark 固定数据资产的函数。
            readiness_checker: 检查原生或 Docker 执行器真实就绪状态的共享函数。
        """
        self._repository = repository
        self._benchmark_executor = benchmark_executor or SubprocessEvaluationExecutor()
        self._asset_preparer = asset_preparer
        self._readiness_checker = readiness_checker

    def execute(
        self,
        task_id: str,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> dict[str, object]:
        """执行任务中的全部可运行节点并返回终结节点产物。"""
        while True:
            if cancel_event.is_set():
                raise TaskExecutionCanceled("evaluation canceled")
            blocked_dependencies = self._block_unsatisfied_nodes(task_id)
            nodes = self._repository.list_nodes(task_id)
            finalizer = next(node for node in nodes if node.kind == "workflow_finalize")
            if finalizer.status in TERMINAL_NODE_STATUSES:
                break
            ready = next((node for node in nodes if self._is_ready(node, nodes)), None)
            if ready is None:
                # 本轮若刚传播阻塞状态，下一轮才能让更下游节点观察到新终态。
                if blocked_dependencies:
                    continue
                raise TaskExecutionError("workflow has no runnable node")
            self._execute_node(
                ready,
                request,
                on_progress=on_progress,
                on_resources=on_resources,
                cancel_event=cancel_event,
            )

        finalizer = self._repository.get_node(finalizer.id)
        if finalizer.status != "success" or finalizer.output is None:
            raise TaskExecutionError(finalizer.error_message or "workflow finalizer failed")
        benchmark_nodes = [
            node for node in self._repository.list_nodes(task_id) if node.kind == "benchmark"
        ]
        if any(node.status != "success" for node in benchmark_nodes):
            raise WorkflowIncompleteError("部分 Benchmark 未完成；能力画像已按成功结果生成")
        return finalizer.output

    def _execute_node(
        self,
        node: EvaluationNode,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> None:
        """执行一个 ready 节点并将成功或分类错误写回仓储。"""
        running = self._repository.start_node(node.id)
        try:
            if running.kind == "prepare_assets":
                output = self._prepare_assets(running)
            elif running.kind == "benchmark":
                output = self._run_benchmark(
                    running,
                    request,
                    on_progress=on_progress,
                    on_resources=on_resources,
                    cancel_event=cancel_event,
                )
            elif running.kind == "capability_aggregate":
                output = self._aggregate(running.task_id)
            elif running.kind == "workflow_finalize":
                output = self._finalize(running.task_id, request)
            else:
                raise RuntimeBlockedError("unknown_node_kind", f"未知节点类型：{running.kind}")
        except TaskExecutionCanceled:
            raise
        except Exception as exc:
            classification = classify_runtime_error(exc)
            error_type = getattr(exc, "error_type", None) or exc.__class__.__name__.lower()
            if classification == "transient" and running.attempt_count < running.max_attempts:
                self._repository.reschedule_node(running.id, str(error_type), str(exc))
            elif classification == "blocked":
                self._repository.block_node(running.id, str(error_type), str(exc))
            else:
                self._repository.fail_node(running.id, str(error_type), str(exc))
            return
        self._repository.complete_node(running.id, output)

    def _prepare_assets(self, node: EvaluationNode) -> dict[str, object]:
        """准备可接通的数据资产，并用共享检查器确认对应执行器真实就绪。

        Args:
            node: 包含固定 Suite 成员 ID 的运行中资产准备节点。

        Returns:
            每个成员的数据路径、摘要、revision 和执行器就绪或失败状态。
        """
        assets: dict[str, object] = {}
        benchmarks = {
            str(item.input["benchmark_id"]): item
            for item in self._repository.list_nodes(node.task_id)
            if item.kind == "benchmark"
        }
        # 先整体核对七条固定来源，防止第三条漂移时前两条已经开始下载。
        self._verify_source_contracts(benchmarks)
        for benchmark in benchmarks.values():
            self._verify_verifier_identity(benchmark)
        for benchmark_id in node.input.get("benchmark_ids", []):
            spec = _frozen_benchmark_spec(benchmarks[str(benchmark_id)])
            readiness = self._readiness_checker(spec)
            supported_source = (
                spec.executor == ExecutorKind.NATIVE or spec.id == "hexagon-humaneval"
            )
            if not supported_source:
                assets[spec.id] = {
                    "status": "unavailable",
                    "error_type": readiness.code,
                    "message": readiness.message,
                }
                continue
            try:
                path = self._asset_preparer(spec.id)
                content_sha256 = _content_sha256(path)
            except Exception as exc:
                assets[spec.id] = {
                    "status": "failed",
                    "error_type": "dataset_prepare_failed",
                    "message": str(exc),
                }
                continue
            # HumanEval 必须同时具备校验后的固定数据和可信 Docker 镜像，缺一即不可评分。
            assets[spec.id] = {
                "status": "ready" if readiness.ready else "unavailable",
                "path": str(path),
                "content_sha256": content_sha256,
                "dataset_revision": (
                    f"sha256:{content_sha256}" if content_sha256 else spec.dataset_revision
                ),
            }
            if not readiness.ready:
                assets[spec.id].update(
                    {"error_type": readiness.code, "message": readiness.message}
                )
        return {"assets": assets}

    def _verify_source_contracts(
        self,
        benchmarks: dict[str, EvaluationNode],
    ) -> None:
        """在任何资产准备前核对 Hexagon 创建合同与当前 Task 2 固定来源。

        Args:
            benchmarks: 当前任务按 Benchmark ID 索引的全部持久化执行节点。

        Raises:
            RuntimeBlockedError: 任一 Hexagon 来源合同缺失或与当前部署记录不一致。
        """
        hexagon_nodes = {
            benchmark_id: benchmark
            for benchmark_id, benchmark in benchmarks.items()
            if benchmark_id.startswith("hexagon-")
        }
        if not hexagon_nodes:
            return
        # 一次读取当前部署目录，保证同一准备预检不会混用两个动态视图。
        current_sources = hexagon_source_specs()
        mismatched: list[EvaluationNode] = []
        for benchmark_id, benchmark in hexagon_nodes.items():
            source = current_sources.get(benchmark_id)
            frozen_contract = benchmark.input.get("source_contract")
            current_contract = (
                {
                    "source_id": source.benchmark_id,
                    "url": source.url,
                    "revision": source.revision,
                    "sha256": source.sha256,
                }
                if source is not None
                else None
            )
            # 任一侧缺失都必须 fail-closed；不能让旧节点通过 ``None == None`` 绕过验证。
            if (
                not isinstance(frozen_contract, dict)
                or current_contract is None
                or frozen_contract != current_contract
            ):
                mismatched.append(benchmark)
        if not mismatched:
            return
        changed_ids = ", ".join(sorted(str(item.input["benchmark_id"]) for item in mismatched))
        message = f"固定来源合同与任务创建时不一致，不能安全准备：{changed_ids}"
        # 异常恢复留下的受影响样本不可进入 skip 集；其他冻结来源的检查点仍可保留审计。
        for benchmark in mismatched:
            self._repository.clear_node_samples(benchmark.id, message)
        raise RuntimeBlockedError("source_contract_changed", message)

    def _verify_verifier_identity(self, benchmark: EvaluationNode) -> None:
        """核对 HumanEval 创建时身份与当前三文件执行上下文，并清除旧检查点。

        Args:
            benchmark: 可能属于 Hexagon HumanEval 的持久化 Benchmark 节点。

        Raises:
            RuntimeBlockedError: HumanEval 身份缺失或与当前部署字节不一致时抛出。
        """
        if benchmark.input.get("benchmark_id") != "hexagon-humaneval":
            return
        frozen_identity = benchmark.input.get("verifier_identity")
        current_identity = humaneval_verifier_identity()
        if isinstance(frozen_identity, str) and frozen_identity == current_identity:
            return
        message = "HumanEval verifier 与任务创建时冻结身份不一致，不能安全恢复"
        self._repository.clear_node_samples(benchmark.id, message)
        raise RuntimeBlockedError("verifier_identity_changed", message)

    def _run_benchmark(
        self,
        node: EvaluationNode,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> dict[str, object]:
        """执行一个已通过准备检查的 Benchmark，并从全部 SQLite 检查点聚合结果。

        Args:
            node: 当前需要执行或恢复的 Benchmark 节点。
            request: 顶层模型评测请求，执行时会替换为当前来源 ID。
            on_progress: 接收汇总后顶层样本进度的回调。
            on_resources: 接收隔离进程资源读数的回调。
            cancel_event: 服务请求取消时由执行器观察的线程事件。

        Returns:
            包含来源 revision、固定生成配置和全量检查点得分的节点输出。

        Raises:
            RuntimeBlockedError: 执行器未就绪或执行期间数据资产发生变化时抛出。
        """
        benchmark_id = str(node.input["benchmark_id"])
        spec = _frozen_benchmark_spec(node)
        protocol_fingerprint = str(node.input["protocol_fingerprint"])
        self._verify_verifier_identity(node)
        prepare_node = next(
            item
            for item in self._repository.list_nodes(node.task_id)
            if item.kind == "prepare_assets"
        )
        asset = ((prepare_node.output or {}).get("assets") or {}).get(benchmark_id, {})
        if asset.get("status") != "ready":
            raise RuntimeBlockedError(
                str(asset.get("error_type", "executor_not_ready")),
                str(asset.get("message", f"{spec.display_name} 当前不可运行")),
            )
        frozen_manifest_sha256 = node.input.get("manifest_sha256")
        if (
            frozen_manifest_sha256 is not None
            and frozen_manifest_sha256 != _hexagon_manifest_sha256()
        ):
            message = "包内 Hexagon 清单与任务创建时冻结摘要不一致，不能安全恢复"
            self._repository.clear_node_samples(node.id, message)
            raise RuntimeBlockedError("manifest_revision_changed", message)

        execution_digest_before = _content_sha256(asset.get("path"))
        prepared_digest = asset.get("content_sha256")
        checkpoint_digest = (node.checkpoint or {}).get("content_sha256")
        baseline_digest = checkpoint_digest or prepared_digest
        existing_samples = self._all_samples(node.id)
        if any(
            not _sample_matches_protocol(sample, protocol_fingerprint)
            for sample in existing_samples
        ):
            self._repository.clear_node_samples(
                node.id,
                "样本检查点协议指纹与任务创建时冻结值不一致，旧结果已失效",
            )
        if baseline_digest and baseline_digest != execution_digest_before:
            self._repository.clear_node_samples(
                node.id,
                "数据资产自准备节点完成后已变化，旧样本检查点已失效",
            )
        skipped = self._repository.completed_sample_keys(node.id)
        benchmark_request = replace(
            request,
            dataset=benchmark_id,
            suite_id=None,
            subject=str(node.input.get("subject", request.subject)),
            generation_config=dict(spec.generation_config),
            evaluator_type=str(node.input["evaluator_type"]),
        )

        def report_progress(completed: int, total: int) -> None:
            """先更新节点真实分母，再汇总为顶层任务样本进度。"""
            self._repository.update_node_progress(node.id, completed=completed, total=total)
            self._report_task_progress(node.task_id, on_progress)

        def report_sample(sample: dict[str, object], completed: int, total: int) -> None:
            """原子保存一个样本结果，并在提交后更新顶层进度。

            Args:
                sample: Benchmark 返回的样本标识、得分和诊断字段。
                completed: 当前节点已完成的样本数。
                total: 当前节点需要执行的样本总数。
            """
            # 已完成评分的样本都可在断点恢复时跳过；未通过样本仍保留失败状态供调试。
            sample_status = "success" if float(sample.get("score", 0.0)) >= 1.0 else "failed"
            # 样本结果与节点进度在仓储内一次提交，避免恢复时读到不一致检查点。
            persisted_result = {**sample, "protocol_fingerprint": protocol_fingerprint}
            self._repository.record_sample(
                node.id,
                EvaluationSampleCheckpoint(
                    node_id=node.id,
                    sample_key=str(sample["sample_id"]),
                    sample_index=max(0, completed - 1),
                    status=sample_status,
                    attempt_count=node.attempt_count,
                    input={
                        "input": sample.get("input"),
                        "reference": sample.get("reference"),
                        "metadata": sample.get("metadata", {}),
                        "protocol_fingerprint": protocol_fingerprint,
                    },
                    result=persisted_result,
                ),
                completed=completed,
                total=total,
                content_sha256=execution_digest_before,
            )
            self._report_task_progress(node.task_id, on_progress)

        self._benchmark_executor.execute(
            node.task_id,
            benchmark_request,
            on_progress=report_progress,
            on_resources=on_resources,
            cancel_event=cancel_event,
            skip_sample_ids=skipped,
            on_sample_result=report_sample,
        )
        execution_digest_after = _content_sha256(asset.get("path"))
        if execution_digest_before != execution_digest_after:
            message = "数据资产在 Benchmark 执行期间发生变化，请重试该节点"
            self._repository.clear_node_samples(node.id, message)
            raise RuntimeBlockedError("dataset_revision_changed", message)
        samples = [
            sample
            for sample in self._all_samples(node.id)
            if _sample_matches_protocol(sample, protocol_fingerprint)
        ]
        scores = [float((sample.result or {}).get("score", 0.0)) for sample in samples]
        return {
            "benchmark_id": benchmark_id,
            "benchmark": spec.display_name,
            "status": "success",
            "model": request.model,
            "metric": spec.metric,
            "dataset_source": spec.dataset_source,
            "dataset_revision": _resolved_dataset_revision(spec, execution_digest_after),
            "expected_sample_count": spec.expected_sample_count,
            "prompt_template_version": spec.prompt_template_version,
            "generation_config": dict(spec.generation_config),
            "protocol_fingerprint": protocol_fingerprint,
            "raw_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
            "score_sum": round(sum(scores), 6),
            "total_samples": len(samples),
            "passed_samples": sum(1 for score in scores if score >= 1.0),
            "failed_sample_ids": [
                sample.sample_key
                for sample, score in zip(samples, scores, strict=True)
                if score < 1.0
            ],
            "failed_examples": [
                dict(sample.result or {})
                for sample, score in zip(samples, scores, strict=True)
                if score < 1.0
            ][:5],
            "protocol_scope": "evalhub_generation",
        }

    def _aggregate(self, task_id: str) -> dict[str, object]:
        """只聚合与创建时冻结协议完全匹配的持久化 Benchmark 输出。"""
        outputs: list[dict[str, object]] = []
        nodes = self._repository.list_nodes(task_id)
        aggregate = next(node for node in nodes if node.kind == "capability_aggregate")
        benchmarks = [node for node in nodes if node.kind == "benchmark"]
        fingerprint = str(aggregate.input["protocol_fingerprint"])
        for node in benchmarks:
            if node.output is not None and node.output.get("protocol_fingerprint") == fingerprint:
                outputs.append(node.output)
            else:
                # 成功节点若带有其他协议的旧输出，只作为阻塞诊断，不能进入当前画像。
                mismatched = node.output is not None
                outputs.append(
                    {
                        "benchmark_id": node.input["benchmark_id"],
                        "status": "blocked" if mismatched else node.status,
                        "error_type": "protocol_mismatch" if mismatched else node.error_type,
                    }
                )
        suite = BenchmarkSuiteSpec(
            id=str(aggregate.input["suite_id"]),
            version=str(aggregate.input["suite_version"]),
            display_name=str(aggregate.input["suite_display_name"]),
            benchmark_ids=tuple(str(item) for item in aggregate.input["benchmark_ids"]),
        )
        specs = tuple(_frozen_benchmark_spec(node) for node in benchmarks)
        return aggregate_capability_profile(suite, outputs, benchmark_specs=specs)

    def _finalize(self, task_id: str, request: TaskRequest) -> dict[str, object]:
        """把节点产物收敛为兼容任务结果，并附加固定协议复现信息。

        Args:
            task_id: 当前持久化顶层任务标识。
            request: 用于恢复 Suite、模型和 adapter 身份的原始请求。

        Returns:
            兼容旧字段且包含能力画像、来源 revision 与生成配置的最终结果。
        """
        nodes = self._repository.list_nodes(task_id)
        benchmarks = [node for node in nodes if node.kind == "benchmark"]
        aggregate = next(node for node in nodes if node.kind == "capability_aggregate")
        finalizer = next(node for node in nodes if node.kind == "workflow_finalize")
        comparison_fingerprint = str(finalizer.input["protocol_fingerprint"])
        successful = [
            node.output
            for node in benchmarks
            if node.output is not None
            and node.output.get("protocol_fingerprint") == comparison_fingerprint
        ]
        total = sum(int(item.get("total_samples", 0)) for item in successful)
        passed = sum(int(item.get("passed_samples", 0)) for item in successful)
        score_sum = sum(float(item.get("score_sum", 0.0)) for item in successful)
        reproducibility = dict(finalizer.input["reproducibility"])
        return {
            "job_id": task_id,
            "status": (
                "success"
                if len(successful) == len(benchmarks)
                and all(node.status == "success" for node in benchmarks)
                else "partial"
            ),
            "dataset": request.dataset,
            "suite_id": request.suite_id,
            "benchmark": str(finalizer.input["suite_display_name"]),
            "model": request.model,
            "adapter": request.adapter,
            "metric": "capability_profile",
            "total_samples": total,
            "passed_samples": passed,
            "average_score": round(score_sum / total, 4) if total else 0.0,
            "failed_sample_ids": [
                sample_id for item in successful for sample_id in item.get("failed_sample_ids", [])
            ],
            "failed_examples": [
                example
                for item in successful
                for example in item.get("failed_examples", [])
            ][:5],
            "capability_profile": aggregate.output,
            "reproducibility": reproducibility,
            "comparison_fingerprint": comparison_fingerprint,
        }

    def _block_unsatisfied_nodes(self, task_id: str) -> bool:
        """把确定无法满足依赖的 pending 节点转为可审计阻塞态。

        Args:
            task_id: 当前持久化工作流所属的顶层任务标识。

        Returns:
            本轮至少阻塞一个节点时返回 ``True``，供执行循环继续传播下游终态。
        """
        nodes = self._repository.list_nodes(task_id)
        by_key = {node.node_key: node for node in nodes}
        benchmarks = [node for node in nodes if node.kind == "benchmark"]
        blocked_any = False
        for node in nodes:
            if node.status != "pending" or node.kind == "workflow_finalize":
                continue
            if node.kind == "capability_aggregate":
                if benchmarks and all(item.status in TERMINAL_NODE_STATUSES for item in benchmarks):
                    if not any(item.status == "success" for item in benchmarks):
                        running = self._repository.start_node(node.id)
                        self._repository.block_node(
                            running.id,
                            "no_successful_benchmark",
                            "没有成功 Benchmark，无法生成能力画像",
                        )
                        blocked_any = True
                continue
            dependencies = [by_key[key] for key in node.depends_on]
            failed = [
                item for item in dependencies if item.status in {"failed", "blocked", "canceled"}
            ]
            if failed:
                running = self._repository.start_node(node.id)
                self._repository.block_node(
                    running.id,
                    "dependency_failed",
                    f"依赖节点未成功：{', '.join(item.node_key for item in failed)}",
                )
                blocked_any = True
        return blocked_any

    @staticmethod
    def _is_ready(node: EvaluationNode, nodes: list[EvaluationNode]) -> bool:
        """根据固定系统节点语义判断 pending 节点是否可执行。"""
        if node.status != "pending":
            return False
        by_key = {item.node_key: item for item in nodes}
        if node.kind == "capability_aggregate":
            benchmarks = [item for item in nodes if item.kind == "benchmark"]
            return (
                bool(benchmarks)
                and all(item.status in TERMINAL_NODE_STATUSES for item in benchmarks)
                and any(item.status == "success" for item in benchmarks)
            )
        if node.kind == "workflow_finalize":
            return all(
                item.id == node.id or item.status in TERMINAL_NODE_STATUSES for item in nodes
            )
        return all(by_key[key].status == "success" for key in node.depends_on)

    def _report_task_progress(
        self,
        task_id: str,
        callback: Callable[[int, int], None],
    ) -> None:
        """汇总全部 Benchmark 节点的样本进度并通知现有任务服务。"""
        benchmarks = [
            node for node in self._repository.list_nodes(task_id) if node.kind == "benchmark"
        ]
        callback(
            sum(node.completed_samples for node in benchmarks),
            sum(node.total_samples for node in benchmarks),
        )

    def _all_samples(self, node_id: str) -> list[EvaluationSampleCheckpoint]:
        """通过稳定游标读取一个节点的全部样本结果。"""
        samples: list[EvaluationSampleCheckpoint] = []
        cursor: str | None = None
        while True:
            page = self._repository.list_samples(node_id, limit=200, cursor=cursor)
            samples.extend(page.items)
            if page.next_cursor is None:
                return samples
            cursor = page.next_cursor


def _content_sha256(value: object) -> str | None:
    """计算真实本地数据文件或目录的确定性 SHA-256。"""
    path = Path(str(value))
    if not path.exists():
        return None
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        if path.is_dir():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
        with item.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _hexagon_manifest_sha256() -> str:
    """读取当前包内 Hexagon 清单摘要，仅用于拒绝跨清单继续执行。

    Returns:
        当前安装包清单原始字节的 SHA-256；最终结果不会使用这一动态值。
    """
    resource = files("evalhub.datasets").joinpath("manifests/hexagon_v1.json")
    return hashlib.sha256(resource.read_bytes()).hexdigest()


def _frozen_benchmark_spec(node: EvaluationNode) -> BenchmarkSpec:
    """从节点输入恢复任务创建时冻结的完整 Benchmark 规格。

    Args:
        node: 包含工作流创建时完整协议字段的 Benchmark 节点。

    Returns:
        不依赖当前 Registry 的不可变执行与归一化规格。
    """
    payload = node.input
    return BenchmarkSpec(
        id=str(payload["benchmark_id"]),
        version=str(payload["benchmark_version"]),
        display_name=str(payload["benchmark_display_name"]),
        capability=Capability(str(payload["capability"])),
        dataset_source=str(payload["dataset_source"]),
        dataset_revision=str(payload["dataset_revision"]),
        homepage=str(payload["homepage"]),
        license=str(payload["license"]),
        expected_sample_count=(
            int(payload["expected_sample_count"])
            if payload.get("expected_sample_count") is not None
            else None
        ),
        executor=ExecutorKind(str(payload["executor"])),
        task_name=str(payload["task_name"]),
        metric=str(payload["metric"]),
        normalization=NormalizationKind(str(payload["normalization"])),
        random_baseline=(
            float(payload["random_baseline"])
            if payload.get("random_baseline") is not None
            else None
        ),
        weight=float(payload["weight"]),
        prompt_template_version=str(payload["prompt_template_version"]),
        few_shot=int(payload["few_shot"]),
        generation_config=dict(payload["generation_config"]),
        requirements=tuple(str(item) for item in payload["requirements"]),
    )


def _sample_matches_protocol(
    sample: EvaluationSampleCheckpoint,
    protocol_fingerprint: str,
) -> bool:
    """检查样本输入和结果是否都属于节点冻结的同一协议。

    Args:
        sample: 从 SQLite 恢复的单条样本检查点。
        protocol_fingerprint: 当前节点创建时冻结的完整协议摘要。

    Returns:
        两个 JSON 边界均精确匹配时返回 ``True``；旧行或混合行返回 ``False``。
    """
    result = sample.result or {}
    return (
        sample.input.get("protocol_fingerprint") == protocol_fingerprint
        and result.get("protocol_fingerprint") == protocol_fingerprint
    )


def _resolved_dataset_revision(spec: BenchmarkSpec, content_sha256: str | None) -> str:
    """为运行时解析型旧数据集补摘要，并保持固定来源 revision 不变。

    Args:
        spec: 创建任务时冻结的 Benchmark 来源规格。
        content_sha256: 执行结束时重新计算的本地资产摘要。

    Returns:
        固定来源使用创建时 revision；旧解析型来源在可用时使用实际文件摘要。
    """
    if spec.dataset_revision == "resolved-at-runtime:sha256" and content_sha256:
        return f"sha256:{content_sha256}"
    return spec.dataset_revision
