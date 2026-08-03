"""验证单 Worker 任务服务的 FIFO、状态更新、失败和取消语义。"""

from collections.abc import Callable
from pathlib import Path
from queue import Queue
from threading import Barrier, Event, Thread
from time import monotonic, sleep

import pytest

from evalhub.tasks import (
    EvaluationTaskService,
    ResourceUsage,
    SQLiteTaskRepository,
    TaskConflictError,
    TaskExecutionCanceled,
    TaskRequest,
)
from evalhub.tasks.workflow import build_workflow


def make_request(dataset: str) -> TaskRequest:
    """创建服务测试使用的完整离线评测请求。

    Args:
        dataset: 用于验证 FIFO 顺序的可辨识数据集名称。

    Returns:
        不会直接触发网络行为的 oracle 请求。
    """
    return TaskRequest(
        dataset=dataset,
        adapter="oracle",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        sample_mode="quick",
        subject="abstract_algebra",
        limit=None,
    )


def result_for(task_id: str, dataset: str) -> dict[str, object]:
    """构造服务成功回调应持久化的最小完整评测结果。"""
    return {
        "job_id": task_id,
        "status": "success",
        "dataset": dataset,
        "benchmark": f"{dataset} benchmark",
        "model": "local-test",
        "adapter": "oracle",
        "metric": "exact_match",
        "total_samples": 2,
        "passed_samples": 2,
        "average_score": 1.0,
        "failed_sample_ids": [],
        "failed_examples": [],
    }


def wait_for_status(
    repository: SQLiteTaskRepository,
    task_id: str,
    expected: str,
    *,
    timeout: float = 2.0,
) -> None:
    """在有限时间内等待后台 Worker 写入目标状态。

    Args:
        repository: 用于观察真实持久化状态的任务仓储。
        task_id: 需要等待的任务标识。
        expected: 期望出现的生命周期状态。
        timeout: 最长等待秒数，超时后测试失败。
    """
    deadline = monotonic() + timeout
    # 短间隔条件轮询避免固定长睡眠，并在失败时保留最后状态供诊断。
    while monotonic() < deadline:
        if repository.get(task_id).status == expected:
            return
        sleep(0.01)
    actual = repository.get(task_id).status
    raise AssertionError(f"task {task_id} stayed {actual}, expected {expected}")


class RecordingExecutor:
    """同步完成任务并记录真实服务调用顺序的可控执行器。"""

    def __init__(self, *, error: str | None = None) -> None:
        """配置成功结果或需要模拟的执行错误。"""
        self.error = error
        self.task_ids: list[str] = []

    def execute(
        self,
        task_id: str,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> dict[str, object]:
        """记录任务、上报进度资源并返回字面量结果或抛出错误。"""
        self.task_ids.append(task_id)
        if self.error is not None:
            raise RuntimeError(self.error)
        # fake 通过公开回调验证服务会把真实执行事件写进仓储。
        on_progress(1, 2)
        on_resources(ResourceUsage(cpu_percent=25.0, memory_bytes=4096))
        on_progress(2, 2)
        return result_for(task_id, request.dataset)


class BlockingExecutor:
    """等待测试释放或取消信号的可控长任务执行器。"""

    def __init__(self) -> None:
        """初始化开始、释放事件和实际执行标识列表。"""
        self.started = Event()
        self.release = Event()
        self.task_ids: list[str] = []

    def execute(
        self,
        task_id: str,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> dict[str, object]:
        """持续观察取消信号，或在释放后返回成功结果。"""
        self.task_ids.append(task_id)
        self.started.set()
        # 条件循环模拟可取消的真实子进程，而不是依赖不可控的固定执行时长。
        while not self.release.wait(0.01):
            if cancel_event.is_set():
                raise TaskExecutionCanceled("evaluation canceled")
        if cancel_event.is_set():
            raise TaskExecutionCanceled("evaluation canceled")
        return result_for(task_id, request.dataset)


class DelayedFirstPutQueue(Queue[str | None]):
    """让第一次入队等待第二次入队完成，用于稳定暴露提交顺序竞态。"""

    def __init__(self) -> None:
        """初始化首次入队标记和第二项完成信号。"""
        super().__init__()
        self._first_started = Event()
        self._second_queued = Event()

    def put(
        self,
        item: str | None,
        block: bool = True,
        timeout: float | None = None,
    ) -> None:
        """旧并发实现先写第二项；串行提交实现超时后保持创建顺序。"""
        if not self._first_started.is_set():
            self._first_started.set()
            self._second_queued.wait(0.1)
            super().put(item, block=block, timeout=timeout)
            return
        super().put(item, block=block, timeout=timeout)
        self._second_queued.set()


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteTaskRepository:
    """为每个服务测试提供隔离的临时 SQLite 仓储。"""
    return SQLiteTaskRepository(tmp_path / "tasks.db")


def test_service_executes_tasks_fifo_and_persists_callbacks(
    repository: SQLiteTaskRepository,
) -> None:
    """单 Worker 应按提交顺序运行，并保存进度与资源回调。"""
    executor = RecordingExecutor()
    service = EvaluationTaskService(repository, executor=executor)
    service.start()
    try:
        first = service.submit(make_request("gsm8k"))
        second = service.submit(make_request("mmlu"))
        wait_for_status(repository, second.id, "success")
    finally:
        service.stop()

    # 执行顺序、结果和资源读数共同证明服务没有只更新内存状态。
    assert executor.task_ids == [first.id, second.id]
    restored = repository.get(first.id)
    assert restored.completed_samples == 2
    assert restored.cpu_percent == 25.0
    assert restored.memory_bytes == 4096
    assert restored.result == result_for(first.id, "gsm8k")


def test_concurrent_submissions_preserve_persisted_fifo_order(
    repository: SQLiteTaskRepository,
) -> None:
    """并发 HTTP 提交也必须按 SQLite 创建顺序进入单 Worker 队列。"""
    service = EvaluationTaskService(repository, executor=RecordingExecutor())
    delayed_queue = DelayedFirstPutQueue()
    service._queue = delayed_queue
    start_barrier = Barrier(3)

    def submit(dataset: str) -> None:
        """等待两个提交线程就绪后同时调用公开提交接口。"""
        start_barrier.wait(timeout=1.0)
        service.submit(make_request(dataset))

    first_thread = Thread(target=submit, args=("gsm8k",))
    second_thread = Thread(target=submit, args=("mmlu",))
    first_thread.start()
    second_thread.start()
    start_barrier.wait(timeout=1.0)
    first_thread.join(timeout=2.0)
    second_thread.join(timeout=2.0)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    persisted_order = [task.id for task in repository.list_pending()]
    queued_order = [delayed_queue.get_nowait(), delayed_queue.get_nowait()]
    assert queued_order == persisted_order


def test_model_submission_persists_generated_workflow_nodes(
    repository: SQLiteTaskRepository,
) -> None:
    """模型评测入队前必须同时持久化系统生成的四节点流程。"""
    service = EvaluationTaskService(repository, executor=RecordingExecutor())

    task = service.submit(make_request("gsm8k"))

    assert [node.node_key for node in service.list_nodes(task.id)] == [
        "prepare_assets",
        "benchmark:gsm8k",
        "capability_aggregate",
        "workflow_finalize",
    ]


def test_agent_submission_keeps_existing_non_registry_execution_path(
    repository: SQLiteTaskRepository,
) -> None:
    """Codex Agent MVP 不应被错误映射为 LLM Benchmark DAG。"""
    service = EvaluationTaskService(repository, executor=RecordingExecutor())
    agent_request = TaskRequest(
        dataset="coding_mini",
        adapter="ollama",
        model="qwen2.5:0.5b",
        base_url="http://127.0.0.1:11434",
        sample_mode="quick",
        subject="",
        limit=None,
        evaluation_type="agent",
        agent_framework="codex",
    )

    task = service.submit(agent_request)

    assert service.list_nodes(task.id) == []


def test_service_recovers_interrupted_workflow_instead_of_failing_task(
    repository: SQLiteTaskRepository,
) -> None:
    """服务重启应恢复模型节点并让原运行任务继续进入执行器。"""
    request = make_request("gsm8k")
    task = repository.create_with_nodes(request, build_workflow(request))
    repository.mark_running(task.id)
    interrupted = repository.start_node(repository.list_nodes(task.id)[0].id)
    executor = RecordingExecutor()
    service = EvaluationTaskService(repository, executor=executor)

    service.start()
    try:
        wait_for_status(repository, task.id, "success")
    finally:
        service.stop()

    assert executor.task_ids == [task.id]
    assert repository.get_node(interrupted.id).status == "pending"
    assert repository.list_node_events(interrupted.id)[-1].event_type == "node_recovered"


def test_retry_node_reopens_failed_task_and_enqueues_it(
    repository: SQLiteTaskRepository,
) -> None:
    """人工重试失败节点应恢复顶层任务并进入唯一 FIFO 队列。"""
    request = make_request("gsm8k")
    task = repository.create_with_nodes(request, build_workflow(request))
    repository.mark_running(task.id)
    node = repository.start_node(repository.list_nodes(task.id)[0].id)
    repository.fail_node(node.id, "timeout", "连接超时")
    repository.mark_failed(task.id, "连接超时")
    service = EvaluationTaskService(repository, executor=RecordingExecutor())

    retried = service.retry_node(task.id, node.id)

    assert retried.status == "pending"
    assert repository.get(task.id).status == "pending"
    assert service._queue.get_nowait() == task.id


def test_cancel_model_task_cancels_unfinished_workflow_nodes(
    repository: SQLiteTaskRepository,
) -> None:
    """取消模型任务时节点列表不能继续显示为等待执行。"""
    service = EvaluationTaskService(repository, executor=RecordingExecutor())
    task = service.submit(make_request("gsm8k"))

    service.cancel(task.id)

    assert {node.status for node in service.list_nodes(task.id)} == {"canceled"}


def test_service_marks_executor_errors_failed(repository: SQLiteTaskRepository) -> None:
    """执行器异常应转成持久化失败任务并保留诊断消息。"""
    service = EvaluationTaskService(repository, executor=RecordingExecutor(error="model offline"))
    service.start()
    try:
        task = service.submit(make_request("gsm8k"))
        wait_for_status(repository, task.id, "failed")
    finally:
        service.stop()

    assert repository.get(task.id).error_message == "model offline"


def test_service_cancels_running_task(repository: SQLiteTaskRepository) -> None:
    """取消运行任务应通知执行器并立即形成不可逆取消终态。"""
    executor = BlockingExecutor()
    service = EvaluationTaskService(repository, executor=executor)
    service.start()
    try:
        task = service.submit(make_request("gsm8k"))
        assert executor.started.wait(1.0)
        canceled = service.cancel(task.id)
        wait_for_status(repository, task.id, "canceled")
    finally:
        executor.release.set()
        service.stop()

    assert canceled.status == "canceled"
    assert executor.task_ids == [task.id]


def test_service_marks_active_task_failed_when_service_stops(
    repository: SQLiteTaskRepository,
) -> None:
    """服务关闭造成的中断应记录为失败诊断，不能冒充用户主动取消。"""
    executor = BlockingExecutor()
    service = EvaluationTaskService(repository, executor=executor)
    service.start()
    task = service.submit(make_request("gsm8k"))
    assert executor.started.wait(1.0)

    service.stop()
    executor.release.set()

    restored = repository.get(task.id)
    assert restored.status == "failed"
    assert restored.error_message == "服务停止导致评测中断"


def test_service_skips_canceled_pending_task(repository: SQLiteTaskRepository) -> None:
    """排队任务取消后即使队列仍含标识也不得交给执行器。"""
    executor = BlockingExecutor()
    service = EvaluationTaskService(repository, executor=executor)
    service.start()
    try:
        running = service.submit(make_request("gsm8k"))
        assert executor.started.wait(1.0)
        pending = service.submit(make_request("mmlu"))
        service.cancel(pending.id)
        executor.release.set()
        wait_for_status(repository, running.id, "success")
        wait_for_status(repository, pending.id, "canceled")
    finally:
        service.stop()

    assert executor.task_ids == [running.id]


def test_service_rejects_canceling_terminal_task(repository: SQLiteTaskRepository) -> None:
    """已完成任务再次取消应返回冲突而不能破坏结果历史。"""
    service = EvaluationTaskService(repository, executor=RecordingExecutor())
    service.start()
    try:
        task = service.submit(make_request("gsm8k"))
        wait_for_status(repository, task.id, "success")
        with pytest.raises(TaskConflictError, match="task is already success"):
            service.cancel(task.id)
    finally:
        service.stop()
