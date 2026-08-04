"""验证持久化工作流节点、审计事件和样本检查点。"""

from pathlib import Path

import pytest

from evalhub.tasks import (
    EvaluationSampleCheckpoint,
    SQLiteTaskRepository,
    TaskRequest,
    TaskStateError,
    WorkflowNodeSpec,
)


def task_request() -> TaskRequest:
    """构造不依赖网络的评测任务请求。"""
    return TaskRequest(
        dataset="gsm8k",
        adapter="oracle",
        model="oracle",
        base_url="http://127.0.0.1:11434",
        sample_mode="all",
        subject="abstract_algebra",
        limit=None,
    )


def workflow_specs() -> tuple[WorkflowNodeSpec, ...]:
    """构造单 Benchmark 评测使用的四节点固定流程。"""
    return (
        WorkflowNodeSpec("prepare_assets", "prepare_assets"),
        WorkflowNodeSpec(
            "benchmark:gsm8k",
            "benchmark",
            depends_on=("prepare_assets",),
            input={"benchmark_id": "gsm8k"},
        ),
        WorkflowNodeSpec(
            "capability_aggregate",
            "capability_aggregate",
            depends_on=("benchmark:gsm8k",),
        ),
        WorkflowNodeSpec(
            "workflow_finalize",
            "workflow_finalize",
            depends_on=("capability_aggregate",),
            max_attempts=1,
        ),
    )


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteTaskRepository:
    """为每个测试提供全新的 SQLite Runtime 仓储。"""
    return SQLiteTaskRepository(tmp_path / "evalhub.db")


def test_create_with_nodes_persists_task_graph_atomically(
    repository: SQLiteTaskRepository,
) -> None:
    """创建任务时应在一次提交中写入固定顺序节点及创建事件。"""
    task = repository.create_with_nodes(task_request(), workflow_specs(), task_id="job_workflow")
    nodes = repository.list_nodes(task.id)

    assert [node.node_key for node in nodes] == [item.node_key for item in workflow_specs()]
    assert nodes[1].depends_on == ("prepare_assets",)
    assert nodes[1].input == {"benchmark_id": "gsm8k"}
    assert all(node.status == "pending" for node in nodes)
    assert [repository.list_node_events(node.id)[0].event_type for node in nodes] == [
        "node_created",
    ] * 4


def test_start_transition_and_event_are_committed_together(
    repository: SQLiteTaskRepository,
) -> None:
    """节点开始运行时快照和审计事件必须具有相同尝试次数。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.list_nodes(task.id)[0]

    running = repository.start_node(node.id)
    event = repository.list_node_events(node.id)[-1]

    assert running.status == "running"
    assert running.attempt_count == 1
    assert running.attempt_started_at is not None
    assert event.event_type == "node_started"
    assert (event.from_status, event.to_status, event.attempt) == (
        "pending",
        "running",
        1,
    )


def test_append_node_event_persists_live_agent_payload_in_order(
    repository: SQLiteTaskRepository,
) -> None:
    """运行节点应按追加顺序持久化 Agent 白名单事件及其样本载荷。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[1].id)

    repository.append_node_event(
        node.id,
        event_type="sample_started",
        actor="benchmark",
        message="Fix pricing.total_with_tax",
        payload={"sample_id": "pricing_total"},
    )
    appended = repository.append_node_event(
        node.id,
        event_type="agent_message",
        actor="codex",
        message="检查文件",
        payload={"sample_id": "pricing_total", "text": "检查文件"},
    )

    events = repository.list_node_events(node.id)
    assert [event.event_type for event in events][-2:] == ["sample_started", "agent_message"]
    assert appended.id == events[-1].id
    assert appended.payload == {"sample_id": "pricing_total", "text": "检查文件"}
    assert appended.actor == "codex"


def test_complete_node_persists_output_duration_and_success_event(
    repository: SQLiteTaskRepository,
) -> None:
    """成功转换应保存产物、累计耗时和不可变成功事件快照。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[0].id)

    completed = repository.complete_node(node.id, {"prepared": ["gsm8k"]})
    event = repository.list_node_events(node.id)[-1]

    assert completed.status == "success"
    assert completed.output == {"prepared": ["gsm8k"]}
    assert completed.elapsed_ms >= 0
    assert event.event_type == "node_succeeded"
    assert event.payload == {"output": {"prepared": ["gsm8k"]}}


def test_record_sample_and_checkpoint_are_atomic(
    repository: SQLiteTaskRepository,
) -> None:
    """样本结果提交后节点进度和成功样本集合必须同步可见。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[1].id)
    sample = EvaluationSampleCheckpoint(
        node_id=node.id,
        sample_key="sample-1",
        sample_index=0,
        status="success",
        attempt_count=1,
        input={"input": "1 + 1"},
        result={"prediction": "2", "reference": "2", "score": 1.0},
    )

    repository.record_sample(node.id, sample, completed=1, total=2)

    assert repository.successful_sample_keys(node.id) == {"sample-1"}
    persisted = repository.get_node(node.id)
    assert (persisted.completed_samples, persisted.total_samples) == (1, 2)
    assert persisted.checkpoint == {"completed_samples": 1, "total_samples": 2}


def test_duplicate_sample_is_upserted_not_counted_twice(
    repository: SQLiteTaskRepository,
) -> None:
    """相同节点和样本键重复上报时只能保留一条最新快照。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[1].id)
    sample = EvaluationSampleCheckpoint(
        node_id=node.id,
        sample_key="sample-1",
        sample_index=0,
        status="success",
        attempt_count=1,
        input={"input": "1 + 1"},
        result={"prediction": "2", "score": 1.0},
    )

    repository.record_sample(node.id, sample, completed=1, total=1)
    repository.record_sample(node.id, sample, completed=1, total=1)
    page = repository.list_samples(node.id, limit=50)

    assert len(page.items) == 1
    assert page.next_cursor is None


def test_sample_pagination_uses_stable_index_and_key_cursor(
    repository: SQLiteTaskRepository,
) -> None:
    """样本分页必须稳定且不重复返回上一页最后一条记录。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[1].id)
    for index in range(3):
        repository.record_sample(
            node.id,
            EvaluationSampleCheckpoint(
                node_id=node.id,
                sample_key=f"sample-{index}",
                sample_index=index,
                status="success",
                attempt_count=1,
                input={"input": str(index)},
                result={"score": 1.0},
            ),
            completed=index + 1,
            total=3,
        )

    first = repository.list_samples(node.id, limit=2)
    second = repository.list_samples(node.id, limit=2, cursor=first.next_cursor)

    assert [item.sample_key for item in first.items] == ["sample-0", "sample-1"]
    assert [item.sample_key for item in second.items] == ["sample-2"]
    assert second.next_cursor is None


def test_node_state_guard_rejects_starting_successful_node(
    repository: SQLiteTaskRepository,
) -> None:
    """成功节点不能再次直接开始，必须经过显式重试语义。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[0].id)
    repository.complete_node(node.id, {})

    with pytest.raises(TaskStateError, match="cannot start success node"):
        repository.start_node(node.id)


def test_failed_and_blocked_nodes_persist_classified_events(
    repository: SQLiteTaskRepository,
) -> None:
    """运行错误必须落为明确终态，并在事件中保存分类和安全消息。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    prepare, benchmark = repository.list_nodes(task.id)[:2]
    repository.start_node(prepare.id)
    failed = repository.fail_node(prepare.id, "timeout", "模型响应超时")
    repository.start_node(benchmark.id)
    blocked = repository.block_node(benchmark.id, "invalid_dataset", "数据字段缺失")

    assert (failed.status, failed.error_type) == ("failed", "timeout")
    assert (blocked.status, blocked.error_type) == ("blocked", "invalid_dataset")
    assert repository.list_node_events(prepare.id)[-1].event_type == "node_failed"
    assert repository.list_node_events(benchmark.id)[-1].event_type == "node_blocked"


def test_retry_node_resets_descendants_and_preserves_sample_checkpoint(
    repository: SQLiteTaskRepository,
) -> None:
    """人工重试应保留 Benchmark 成功样本，同时清除后继聚合产物。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    prepare, benchmark, aggregate, finalize = repository.list_nodes(task.id)
    repository.start_node(prepare.id)
    repository.complete_node(prepare.id, {"prepared": True})
    repository.start_node(benchmark.id)
    repository.record_sample(
        benchmark.id,
        EvaluationSampleCheckpoint(
            node_id=benchmark.id,
            sample_key="sample-1",
            sample_index=0,
            status="success",
            attempt_count=1,
            input={"input": "1 + 1"},
            result={"score": 1.0},
        ),
        completed=1,
        total=2,
    )
    repository.fail_node(benchmark.id, "timeout", "模型响应超时")

    reset = repository.retry_node(task.id, benchmark.id)

    assert reset.status == "pending"
    assert reset.completed_samples == 1
    assert repository.successful_sample_keys(benchmark.id) == {"sample-1"}
    assert repository.get_node(aggregate.id).status == "pending"
    assert repository.get_node(finalize.id).status == "pending"
    assert repository.list_node_events(benchmark.id)[-1].event_type == "node_retried"


def test_recover_running_nodes_requeues_when_attempts_remain(
    repository: SQLiteTaskRepository,
) -> None:
    """服务重启后运行节点应回到待执行并保留已提交样本。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    node = repository.start_node(repository.list_nodes(task.id)[1].id)

    recovered = repository.recover_running_nodes()

    assert recovered == 1
    assert repository.get_node(node.id).status == "pending"
    assert repository.list_node_events(node.id)[-1].event_type == "node_recovered"


def test_cancel_nodes_preserves_success_and_cancels_unfinished(
    repository: SQLiteTaskRepository,
) -> None:
    """取消整任务时成功节点应保留，未完成节点统一进入取消态。"""
    task = repository.create_with_nodes(task_request(), workflow_specs())
    nodes = repository.list_nodes(task.id)
    repository.start_node(nodes[0].id)
    repository.complete_node(nodes[0].id, {})

    changed = repository.cancel_nodes(task.id)

    assert changed == 3
    assert repository.get_node(nodes[0].id).status == "success"
    assert {repository.get_node(node.id).status for node in nodes[1:]} == {"canceled"}
