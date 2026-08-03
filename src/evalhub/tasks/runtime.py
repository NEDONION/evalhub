"""执行由系统生成的持久化评测节点并维护断点、重试和能力画像。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

from evalhub.benchmarks import ExecutorKind, aggregate_capability_profile, get_benchmark_spec
from evalhub.datasets import prepare_dataset
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
from evalhub.tasks.workflow import workflow_suite

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
    ) -> None:
        """注入仓储、隔离 Benchmark 执行器和可测试的数据准备函数。"""
        self._repository = repository
        self._benchmark_executor = benchmark_executor or SubprocessEvaluationExecutor()
        self._asset_preparer = asset_preparer

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
            self._block_unsatisfied_nodes(task_id)
            nodes = self._repository.list_nodes(task_id)
            finalizer = next(node for node in nodes if node.kind == "workflow_finalize")
            if finalizer.status in TERMINAL_NODE_STATUSES:
                break
            ready = next((node for node in nodes if self._is_ready(node, nodes)), None)
            if ready is None:
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
                output = self._aggregate(running.task_id, request)
            elif running.kind == "workflow_finalize":
                output = self._finalize(running.task_id, request)
            else:
                raise RuntimeBlockedError("unknown_node_kind", f"未知节点类型：{running.kind}")
        except TaskExecutionCanceled:
            raise
        except Exception as exc:
            classification = classify_runtime_error(exc)
            error_type = getattr(exc, "error_type", exc.__class__.__name__.lower())
            if classification == "transient" and running.attempt_count < running.max_attempts:
                self._repository.reschedule_node(running.id, str(error_type), str(exc))
            elif classification == "blocked":
                self._repository.block_node(running.id, str(error_type), str(exc))
            else:
                self._repository.fail_node(running.id, str(error_type), str(exc))
            return
        self._repository.complete_node(running.id, output)

    def _prepare_assets(self, node: EvaluationNode) -> dict[str, object]:
        """检查每个 Suite 成员的本地执行器和数据准备状态。"""
        assets: dict[str, object] = {}
        for benchmark_id in node.input.get("benchmark_ids", []):
            spec = get_benchmark_spec(str(benchmark_id))
            if spec.executor != ExecutorKind.NATIVE:
                assets[spec.id] = {
                    "status": "unavailable",
                    "error_type": "executor_not_ready",
                    "message": f"本地尚未配置 {spec.executor.value} 执行器",
                }
                continue
            try:
                path = self._asset_preparer(spec.id)
                content_sha256 = _content_sha256(path)
                assets[spec.id] = {
                    "status": "ready",
                    "path": str(path),
                    "content_sha256": content_sha256,
                    "dataset_revision": (
                        f"sha256:{content_sha256}" if content_sha256 else spec.dataset_revision
                    ),
                }
            except Exception as exc:
                assets[spec.id] = {
                    "status": "failed",
                    "error_type": "dataset_prepare_failed",
                    "message": str(exc),
                }
        return {"assets": assets}

    def _run_benchmark(
        self,
        node: EvaluationNode,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> dict[str, object]:
        """执行一个本地原生 Benchmark，并把每条样本即时写入 SQLite。"""
        benchmark_id = str(node.input["benchmark_id"])
        spec = get_benchmark_spec(benchmark_id)
        prepare_node = next(
            item
            for item in self._repository.list_nodes(node.task_id)
            if item.kind == "prepare_assets"
        )
        asset = ((prepare_node.output or {}).get("assets") or {}).get(benchmark_id, {})
        if spec.executor != ExecutorKind.NATIVE or asset.get("status") != "ready":
            raise RuntimeBlockedError(
                str(asset.get("error_type", "executor_not_ready")),
                str(asset.get("message", f"{spec.display_name} 当前不可运行")),
            )

        execution_digest_before = _content_sha256(asset.get("path"))
        prepared_digest = asset.get("content_sha256")
        checkpoint_digest = (node.checkpoint or {}).get("content_sha256")
        baseline_digest = checkpoint_digest or prepared_digest
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
            self._repository.record_sample(
                node.id,
                EvaluationSampleCheckpoint(
                    node_id=node.id,
                    sample_key=str(sample["sample_id"]),
                    sample_index=max(0, completed - 1),
                    status=sample_status,
                    attempt_count=node.attempt_count,
                    input={"input": sample.get("input"), "reference": sample.get("reference")},
                    result=sample,
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
        samples = self._all_samples(node.id)
        scores = [float((sample.result or {}).get("score", 0.0)) for sample in samples]
        metric = (
            str((samples[0].result or {}).get("metric", spec.metric)) if samples else spec.metric
        )
        return {
            "benchmark_id": benchmark_id,
            "benchmark": spec.display_name,
            "status": "success",
            "model": request.model,
            "metric": metric,
            "dataset_source": spec.dataset_source,
            "dataset_revision": (
                f"sha256:{execution_digest_after}"
                if execution_digest_after
                else asset.get("dataset_revision", spec.dataset_revision)
            ),
            "raw_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
            "score_sum": round(sum(scores), 6),
            "total_samples": len(samples),
            "passed_samples": sum(1 for score in scores if score >= 1.0),
            "failed_sample_ids": [
                sample.sample_key
                for sample, score in zip(samples, scores, strict=True)
                if score < 1.0
            ],
            "protocol_scope": "evalhub_generation",
        }

    def _aggregate(self, task_id: str, request: TaskRequest) -> dict[str, object]:
        """只读取持久化 Benchmark 输出并生成固定六维能力画像。"""
        outputs: list[dict[str, object]] = []
        for node in self._repository.list_nodes(task_id):
            if node.kind != "benchmark":
                continue
            if node.output is not None:
                outputs.append(node.output)
            else:
                outputs.append(
                    {
                        "benchmark_id": node.input["benchmark_id"],
                        "status": node.status,
                        "error_type": node.error_type,
                    }
                )
        return aggregate_capability_profile(workflow_suite(request), outputs)

    def _finalize(self, task_id: str, request: TaskRequest) -> dict[str, object]:
        """把 Benchmark 与画像节点产物收敛为现有任务结果兼容结构。"""
        nodes = self._repository.list_nodes(task_id)
        benchmarks = [node for node in nodes if node.kind == "benchmark"]
        successful = [node.output for node in benchmarks if node.output is not None]
        aggregate = next(node for node in nodes if node.kind == "capability_aggregate")
        total = sum(int(item.get("total_samples", 0)) for item in successful)
        passed = sum(int(item.get("passed_samples", 0)) for item in successful)
        score_sum = sum(float(item.get("score_sum", 0.0)) for item in successful)
        suite = workflow_suite(request)
        return {
            "job_id": task_id,
            "status": (
                "success" if all(node.status == "success" for node in benchmarks) else "partial"
            ),
            "dataset": request.dataset,
            "suite_id": request.suite_id,
            "benchmark": suite.display_name,
            "model": request.model,
            "adapter": request.adapter,
            "metric": "capability_profile",
            "total_samples": total,
            "passed_samples": passed,
            "average_score": round(score_sum / total, 4) if total else 0.0,
            "failed_sample_ids": [
                sample_id for item in successful for sample_id in item.get("failed_sample_ids", [])
            ],
            "failed_examples": [],
            "capability_profile": aggregate.output,
        }

    def _block_unsatisfied_nodes(self, task_id: str) -> None:
        """把确定无法满足依赖的 pending 节点转为可审计阻塞态。"""
        nodes = self._repository.list_nodes(task_id)
        by_key = {node.node_key: node for node in nodes}
        benchmarks = [node for node in nodes if node.kind == "benchmark"]
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
