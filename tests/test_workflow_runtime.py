"""验证系统生成工作流、节点执行、自动重试和部分能力画像。"""

import hashlib
from pathlib import Path
from threading import Event

import pytest

from evalhub.tasks import ResourceUsage, SQLiteTaskRepository, TaskRequest
from evalhub.tasks.executor import TaskExecutionError
from evalhub.tasks.runtime import PersistentWorkflowExecutor, WorkflowIncompleteError
from evalhub.tasks.workflow import build_workflow


def request(*, suite_id: str | None = None, subject: str = "abstract_algebra") -> TaskRequest:
    """构造单项或 Suite 的离线 Oracle 请求。"""
    return TaskRequest(
        dataset="gsm8k",
        adapter="oracle",
        model="oracle",
        base_url="http://127.0.0.1:11434",
        sample_mode="all",
        subject=subject,
        limit=None,
        suite_id=suite_id,
    )


class FakeBenchmarkExecutor:
    """按数据集生成确定性样本事件的内存 Benchmark 执行器。"""

    def __init__(self, *, failures_before_success: int = 0, score: float = 1.0) -> None:
        """配置执行器的瞬时失败次数和固定样本得分。

        Args:
            failures_before_success: 成功返回前需要抛出的可重试错误次数。
            score: 每次样本回调返回的固定得分。
        """
        self.failures_before_success = failures_before_success
        self.score = score
        # 调用次数和恢复跳过集合用于断言重试及断点续跑行为。
        self.attempts: dict[str, int] = {}
        self.seen_skips: list[set[str]] = []

    def execute(
        self,
        task_id: str,
        task_request: TaskRequest,
        *,
        on_progress: object,
        on_resources: object,
        cancel_event: Event,
        skip_sample_ids: set[str] | frozenset[str] = frozenset(),
        on_sample_result: object = None,
    ) -> dict[str, object]:
        """发送一条固定得分样本，并按配置模拟可重试连接错误。

        Args:
            task_id: 当前持久化任务标识。
            task_request: 包含待运行数据集的任务请求。
            on_progress: 节点进度回调。
            on_resources: 资源采样回调。
            cancel_event: 调用方提供的取消事件。
            skip_sample_ids: 恢复时无需再次执行的成功样本标识。
            on_sample_result: 单条样本结果回调。

        Returns:
            包含任务标识和样本总数的最小结果摘要。

        Raises:
            TaskExecutionError: 尚未达到配置的成功尝试次数时抛出。
        """
        dataset = task_request.dataset
        self.attempts[dataset] = self.attempts.get(dataset, 0) + 1
        self.seen_skips.append(set(skip_sample_ids))
        # 在指定次数内模拟连接中断，验证运行时的自动重试路径。
        if self.attempts[dataset] <= self.failures_before_success:
            raise TaskExecutionError("connection reset by peer")
        # 测试执行器要求生产调用方完整提供三类可观察回调。
        assert callable(on_progress)
        assert callable(on_resources)
        assert callable(on_sample_result)
        # 恢复后的进度从已成功样本数开始，并提供一条稳定资源采样。
        on_progress(len(skip_sample_ids), 1)
        on_resources(ResourceUsage(cpu_percent=5.0, memory_bytes=1024))
        # 未被恢复逻辑跳过时才发送样本，得分决定成功或失败语义。
        if f"{dataset}-sample-1" not in skip_sample_ids:
            on_sample_result(
                {
                    "sample_id": f"{dataset}-sample-1",
                    "input": "1 + 1",
                    "prediction": "2",
                    "reference": "2",
                    "metric": "exact_match",
                    "score": self.score,
                    "reason": None if self.score >= 1.0 else "答案不匹配",
                },
                1,
                1,
            )
        # 摘要只服务运行时节点终态，本测试不需要模拟更多指标。
        return {"job_id": task_id, "total_samples": 1}


class CheckpointThenRetryExecutor(FakeBenchmarkExecutor):
    """先提交一个零分结果再模拟瞬时中断，验证检查点不会被重跑覆盖。"""

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """首轮提交结果后失败，第二轮必须直接复用该结果。"""
        dataset = task_request.dataset
        self.attempts[dataset] = self.attempts.get(dataset, 0) + 1
        skipped = set(kwargs["skip_sample_ids"])
        self.seen_skips.append(skipped)
        if not skipped:
            callback = kwargs["on_sample_result"]
            assert callable(callback)
            callback(
                {
                    "sample_id": f"{dataset}-sample-1",
                    "input": "1 + 1",
                    "prediction": "3",
                    "reference": "2",
                    "metric": "exact_match",
                    "score": 0.0,
                    "reason": "答案不匹配",
                },
                1,
                1,
            )
            raise TaskExecutionError("connection reset by peer")
        return {"job_id": task_id, "total_samples": 1}


class MutatingAssetExecutor(FakeBenchmarkExecutor):
    """执行后修改资产，用于证明 Runtime 会拒绝不一致的数据 revision。"""

    def __init__(self, asset: Path) -> None:
        """保存需要在评测窗口内改写的文件。"""
        super().__init__()
        self.asset = asset

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """先完成样本事件，再原子窗口内改写资产内容。"""
        result = super().execute(task_id, task_request, **kwargs)
        self.asset.write_text("changed", encoding="utf-8")
        return result


class MutatingRetryExecutor(FakeBenchmarkExecutor):
    """首轮检查点后改变资产并重试，用于验证旧样本不会进入 skip 集。"""

    def __init__(self, asset: Path) -> None:
        """保存资产路径和执行次数。"""
        super().__init__()
        self.asset = asset

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """第一次写入检查点后中断，第二次应在清空检查点后重新评分。"""
        dataset = task_request.dataset
        self.attempts[dataset] = self.attempts.get(dataset, 0) + 1
        skipped = set(kwargs["skip_sample_ids"])
        self.seen_skips.append(skipped)
        callback = kwargs["on_sample_result"]
        assert callable(callback)
        if self.attempts[dataset] == 1:
            callback(
                {
                    "sample_id": f"{dataset}-sample-1",
                    "input": "1 + 1",
                    "prediction": "3",
                    "reference": "2",
                    "metric": "exact_match",
                    "score": 0.0,
                    "reason": "答案不匹配",
                },
                1,
                1,
            )
            self.asset.write_text("changed", encoding="utf-8")
            raise TaskExecutionError("connection reset by peer")
        callback(
            {
                "sample_id": f"{dataset}-sample-1",
                "input": "1 + 1",
                "prediction": "2",
                "reference": "2",
                "metric": "exact_match",
                "score": 1.0,
                "reason": None,
            },
            1,
            1,
        )
        return {"job_id": task_id, "total_samples": 1}


class MutatingDoubleRetryExecutor(MutatingRetryExecutor):
    """资产变化后的新 revision 再中断一次，用于验证检查点能跨后续重试复用。"""

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """前两次写入后中断；第三次应跳过第二次在新 revision 上完成的样本。"""
        result = super().execute(task_id, task_request, **kwargs)
        if self.attempts[task_request.dataset] == 2:
            raise TaskExecutionError("connection reset by peer")
        return result


def test_single_benchmark_builds_fixed_four_node_graph() -> None:
    """兼容请求应映射为准备、单项、聚合和终结四个节点。"""
    graph = build_workflow(request())

    assert [node.node_key for node in graph] == [
        "prepare_assets",
        "benchmark:gsm8k",
        "capability_aggregate",
        "workflow_finalize",
    ]
    assert graph[2].depends_on == ("benchmark:gsm8k",)


def test_suite_builds_one_benchmark_node_per_registry_member() -> None:
    """行业 Suite 必须保持 Registry 顺序生成全部 Benchmark 节点。"""
    graph = build_workflow(request(suite_id="llm-industry-core-v1"))
    benchmark_keys = [node.node_key for node in graph if node.kind == "benchmark"]

    assert benchmark_keys[0] == "benchmark:mmlu-pro"
    assert "benchmark:gsm8k" in benchmark_keys
    assert "benchmark:humaneval" in benchmark_keys
    assert graph[-2].node_key == "capability_aggregate"
    assert graph[-1].node_key == "workflow_finalize"


def test_suite_freezes_mmlu_to_all_subjects() -> None:
    """版本化行业 Suite 不能继承单项表单中残留的 MMLU 学科。"""
    graph = build_workflow(request(suite_id="llm-industry-core-v1", subject="abstract_algebra"))
    mmlu = next(node for node in graph if node.node_key == "benchmark:mmlu")

    assert mmlu.input["subject"] == "all"


def test_runtime_persists_samples_and_completes_single_benchmark(tmp_path: Path) -> None:
    """单项 Oracle 流程应成功持久化样本、画像和最终结果。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = FakeBenchmarkExecutor()
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text('{"question":"1+1","answer":"2"}\n', encoding="utf-8")
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: asset,
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    nodes = repository.list_nodes(task.id)
    benchmark = next(node for node in nodes if node.kind == "benchmark")
    prepare = next(node for node in nodes if node.kind == "prepare_assets")
    aggregate = next(node for node in nodes if node.kind == "capability_aggregate")

    assert {node.status for node in nodes} == {"success"}
    assert repository.successful_sample_keys(benchmark.id) == {"gsm8k-sample-1"}
    expected_digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    assert prepare.output["assets"]["gsm8k"]["content_sha256"] == expected_digest
    assert benchmark.output["dataset_revision"] == f"sha256:{expected_digest}"
    assert aggregate.output is not None
    assert aggregate.output["status"] == "partial"
    assert aggregate.output["capabilities"]["mathematics"]["score"] == 100.0
    assert aggregate.output["capabilities"]["knowledge"]["score"] is None
    assert result["total_samples"] == 1
    assert result["average_score"] == 1.0


def test_runtime_retries_transient_benchmark_error_three_times(tmp_path: Path) -> None:
    """瞬时连接错误应回到 pending，并在第三次尝试成功。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = FakeBenchmarkExecutor(failures_before_success=2)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    benchmark = next(node for node in repository.list_nodes(task.id) if node.kind == "benchmark")

    assert fake.attempts == {"gsm8k": 3}
    assert benchmark.status == "success"
    assert benchmark.attempt_count == 3
    assert [event.event_type for event in repository.list_node_events(benchmark.id)].count(
        "node_retry_scheduled"
    ) == 2


def test_runtime_marks_scored_failure_as_completed_debuggable_sample(tmp_path: Path) -> None:
    """得分未通过的样本应进入失败分页，但断点恢复时不能重新推理。

    Args:
        tmp_path: pytest 提供的隔离目录，用于创建临时 SQLite 仓储。
    """
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    # 固定返回零分样本，直接覆盖失败判定而不依赖真实模型或网络。
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=FakeBenchmarkExecutor(score=0.0),
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    # 完整执行持久化工作流，确保样本状态经过真实运行时和仓储边界。
    runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    # 读取 Benchmark 节点的恢复集合和失败分页，验证两个公开查询保持一致。
    benchmark = next(node for node in repository.list_nodes(task.id) if node.kind == "benchmark")
    failed_page = repository.list_samples(benchmark.id, status="failed")

    assert repository.successful_sample_keys(benchmark.id) == set()
    assert repository.completed_sample_keys(benchmark.id) == {"gsm8k-sample-1"}
    assert [sample.sample_key for sample in failed_page.items] == ["gsm8k-sample-1"]
    assert failed_page.items[0].result["reason"] == "答案不匹配"


def test_runtime_retry_reuses_completed_zero_score_checkpoint(tmp_path: Path) -> None:
    """瞬时错误后的节点重试必须复用已经评分的零分样本。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = CheckpointThenRetryExecutor()
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert fake.seen_skips == [set(), {"gsm8k-sample-1"}]
    assert result["average_score"] == 0.0


def test_runtime_blocks_when_dataset_changes_during_benchmark(tmp_path: Path) -> None:
    """评测窗口内资产摘要变化时不能产出声称可复现的 Benchmark 结果。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("original", encoding="utf-8")
    repository.mark_running(task.id)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=MutatingAssetExecutor(asset),
        asset_preparer=lambda benchmark_id: asset,
    )

    with pytest.raises(WorkflowIncompleteError):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: repository.update_progress(
                task.id, completed=completed, total=total
            ),
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )

    benchmark = next(node for node in repository.list_nodes(task.id) if node.kind == "benchmark")
    assert benchmark.status == "blocked"
    assert benchmark.error_type == "dataset_revision_changed"
    assert repository.completed_sample_keys(benchmark.id) == set()
    assert repository.get(task.id).completed_samples == 0
    assert repository.get(task.id).total_samples == 0


def test_runtime_invalidates_checkpoints_when_asset_changes_between_retries(
    tmp_path: Path,
) -> None:
    """准备后发生资产变化时，旧 revision 的样本不能被恢复逻辑跳过。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("original", encoding="utf-8")
    fake = MutatingRetryExecutor(asset)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: asset,
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert fake.seen_skips == [set(), set()]
    assert result["average_score"] == 1.0


def test_runtime_reuses_new_revision_checkpoints_on_later_retry(tmp_path: Path) -> None:
    """资产切换到新 revision 后形成的检查点必须在下一次瞬时重试中复用。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("original", encoding="utf-8")
    fake = MutatingDoubleRetryExecutor(asset)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: asset,
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert fake.seen_skips == [set(), set(), {"gsm8k-sample-1"}]
    assert result["average_score"] == 1.0


def test_core_suite_blocks_unavailable_executors_but_builds_partial_profile(
    tmp_path: Path,
) -> None:
    """本地缺少外部执行器时应保留原生结果并产生部分画像。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="llm-industry-core-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=FakeBenchmarkExecutor(),
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    with pytest.raises(WorkflowIncompleteError, match="部分 Benchmark 未完成"):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: None,
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )
    nodes = repository.list_nodes(task.id)
    profile = next(node.output for node in nodes if node.kind == "capability_aggregate")

    assert next(node for node in nodes if node.node_key == "benchmark:gsm8k").status == "success"
    assert (
        next(node for node in nodes if node.node_key == "benchmark:humaneval").status == "blocked"
    )
    assert profile is not None
    assert profile["status"] == "partial"
    assert profile["capabilities"]["coding"]["score"] is None
