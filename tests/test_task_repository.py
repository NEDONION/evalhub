"""验证 SQLite 评测任务仓储的持久化、状态约束和重启恢复。"""

from collections.abc import Callable
from pathlib import Path
from threading import Barrier, Thread

import pytest

from evalhub.tasks import (
    ResourceUsage,
    SQLiteTaskRepository,
    TaskNotFoundError,
    TaskRequest,
    TaskStateError,
)


def task_request(dataset: str = "gsm8k") -> TaskRequest:
    """构造无需网络即可持久化的完整评测请求。

    Args:
        dataset: 用于区分任务顺序和恢复断言的数据集名称。

    Returns:
        包含当前 API 全部稳定字段的任务请求。
    """
    # 测试固定使用 oracle，避免仓储用例意外触发真实 Ollama 依赖。
    return TaskRequest(
        dataset=dataset,
        adapter="oracle",
        model="qwen2.5:0.5b",
        base_url="http://127.0.0.1:11434",
        sample_mode="custom",
        subject="abstract_algebra",
        limit=5,
    )


def evaluation_result(task_id: str) -> dict[str, object]:
    """构造仓储成功终态需要保存的真实结果形状。

    Args:
        task_id: 结果必须关联的稳定任务标识。

    Returns:
        同步评测接口当前公开的完整 JSON 兼容结果。
    """
    # 字面量独立于仓储实现，可捕获字段丢失或错误 JSON 往返转换。
    return {
        "job_id": task_id,
        "status": "success",
        "dataset": "gsm8k",
        "benchmark": "GSM8K 测试集",
        "model": "qwen2.5:0.5b",
        "adapter": "oracle",
        "metric": "numeric_exact_match",
        "total_samples": 5,
        "passed_samples": 4,
        "average_score": 0.8,
        "failed_sample_ids": ["sample_5"],
        "failed_examples": [],
    }


def test_repository_persists_progress_resources_and_result(tmp_path: Path) -> None:
    """重新打开数据库后仍应获得完整进度、峰值资源和评测结果。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task = repository.create(task_request(), task_id="job_persisted")
    repository.mark_running(task.id)
    repository.update_progress(task.id, completed=2, total=5)

    # 两次采样验证当前值可以回落，而峰值必须保留任务生命周期内最大值。
    repository.update_resources(
        task.id,
        ResourceUsage(
            cpu_percent=42.5,
            memory_bytes=2048,
            gpu_supported=True,
            gpu_percent=70.0,
            gpu_memory_bytes=4096,
        ),
    )
    repository.update_resources(
        task.id,
        ResourceUsage(
            cpu_percent=10.0,
            memory_bytes=1024,
            gpu_supported=True,
            gpu_percent=20.0,
            gpu_memory_bytes=2048,
        ),
    )

    # 成功写入应把完成数量校准为结果总数，并在新仓储实例中恢复完整正文。
    expected_result = evaluation_result(task.id)
    repository.mark_success(task.id, expected_result)
    restored = SQLiteTaskRepository(tmp_path / "tasks.db").get(task.id)

    assert restored.status == "success"
    assert restored.completed_samples == 5
    assert restored.total_samples == 5
    assert restored.cpu_percent == 10.0
    assert restored.peak_cpu_percent == 42.5
    # 内存与 GPU 当前值、峰值和结果正文共同验证所有持久化列都被正确映射。
    assert restored.memory_bytes == 1024
    assert restored.peak_memory_bytes == 2048
    assert restored.gpu_supported is True
    assert restored.peak_gpu_percent == 70.0
    assert restored.peak_gpu_memory_bytes == 4096
    assert restored.result == expected_result


def test_repository_lists_newest_first_without_full_results(tmp_path: Path) -> None:
    """任务列表应按创建时间倒序返回轻量摘要而不加载结果正文。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    first = repository.create(task_request("gsm8k"), task_id="job_first")
    second = repository.create(task_request("mmlu"), task_id="job_second")

    # 给较早任务写入大结果，列表仍只暴露摘要字段，详情才保留完整结果。
    repository.mark_running(first.id)
    repository.update_progress(first.id, completed=0, total=5)
    repository.mark_success(first.id, evaluation_result(first.id))
    listed = repository.list()

    assert [task.id for task in listed] == [second.id, first.id]
    assert listed[1].result is None
    assert listed[1].average_score == 0.8
    assert repository.get(first.id).result == evaluation_result(first.id)


def test_repository_recovers_interrupted_and_preserves_pending_fifo(tmp_path: Path) -> None:
    """服务重启应失败化运行中任务，并按原创建顺序恢复排队任务。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    running = repository.create(task_request(), task_id="job_running")
    pending_first = repository.create(task_request("mmlu"), task_id="job_pending_first")
    pending_second = repository.create(task_request(), task_id="job_pending_second")
    repository.mark_running(running.id)

    # 恢复动作只改变无法继续的运行态，排队记录保持原顺序等待重新入队。
    recovered_count = repository.recover_interrupted("服务重启导致评测中断")
    pending = repository.list_pending()

    assert recovered_count == 1
    assert repository.get(running.id).status == "failed"
    assert repository.get(running.id).error_message == "服务重启导致评测中断"
    assert [task.id for task in pending] == [pending_first.id, pending_second.id]


def test_repository_rejects_missing_and_terminal_state_rewrites(tmp_path: Path) -> None:
    """缺失任务和终态重写必须抛出明确异常，不能静默破坏历史。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")

    # 缺失标识应转换成仓储级异常，使 HTTP 边界能够稳定映射为 404。
    with pytest.raises(TaskNotFoundError, match="task not found: missing"):
        repository.get("missing")

    task = repository.create(task_request(), task_id="job_terminal")
    repository.mark_canceled(task.id)

    # 已取消任务属于不可逆终态，任何成功覆盖都应被状态守卫拒绝。
    with pytest.raises(TaskStateError, match="cannot transition canceled task to success"):
        repository.mark_success(task.id, evaluation_result(task.id))


def test_repository_allows_only_one_concurrent_terminal_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功与取消并发竞争时只能提交一个终态，结果字段必须与最终状态一致。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task = repository.create(task_request(), task_id="job_terminal_race")
    repository.mark_running(task.id)
    validation_barrier = Barrier(2)
    original_require_status = repository._require_status

    def synchronized_require_status(*args: object, **kwargs: object) -> str:
        """让旧实现的两个状态读取同时完成，以稳定暴露校验与更新分离的竞态。"""
        current = original_require_status(*args, **kwargs)
        action = str(args[3]) if len(args) > 3 else str(kwargs.get("action", ""))
        connection = args[0]
        if action.startswith("transition to ") and not connection.in_transaction:
            validation_barrier.wait(timeout=1.0)
        return current

    monkeypatch.setattr(repository, "_require_status", synchronized_require_status)
    errors: list[Exception] = []

    def run_transition(action: Callable[[], object]) -> None:
        """在线程中执行一个终态动作并收集预期的状态冲突。"""
        try:
            action()
        except Exception as exc:
            errors.append(exc)

    success_thread = Thread(
        target=run_transition,
        args=(lambda: repository.mark_success(task.id, evaluation_result(task.id)),),
    )
    cancel_thread = Thread(target=run_transition, args=(lambda: repository.mark_canceled(task.id),))
    success_thread.start()
    cancel_thread.start()
    success_thread.join(timeout=2.0)
    cancel_thread.join(timeout=2.0)

    assert not success_thread.is_alive()
    assert not cancel_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], TaskStateError)
    restored = repository.get(task.id)
    assert (restored.status == "success") is (restored.result is not None)


def test_repository_round_trips_agent_request_fields(tmp_path: Path) -> None:
    """Agent 请求写入 JSON 后应完整恢复类型和固定 Codex 框架。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    request = TaskRequest(
        dataset="coding_mini",
        adapter="ollama",
        model="qwen2.5-coder:7b",
        base_url="http://127.0.0.1:11434",
        sample_mode="quick",
        subject="",
        limit=None,
        evaluation_type="agent",
        agent_framework="codex",
    )

    # 仓储继续复用 request_json，无需为 Agent 字段增加新的数据库列或迁移。
    restored = repository.create(request, task_id="job_agent")
    assert restored.request.evaluation_type == "agent"
    assert restored.request.agent_framework == "codex"
    assert restored.request.dataset == "coding_mini"
