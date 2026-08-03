"""把持久化任务快照转换为稳定的 HTTP API 响应结构。"""

from dataclasses import asdict
from datetime import datetime

from evalhub.domain.entities import utc_now
from evalhub.tasks.models import (
    EvaluationNode,
    EvaluationNodeEvent,
    EvaluationSampleCheckpoint,
    EvaluationSamplePage,
    EvaluationTask,
)


def task_summary(task: EvaluationTask, *, now: datetime | None = None) -> dict[str, object]:
    """构建不含完整结果正文的任务列表项。

    Args:
        task: 从仓储读取的任务状态快照。
        now: 运行态耗时计算基准；测试可传入固定 UTC 时间。

    Returns:
        包含身份、进度、时间、资源与可选结果摘要的 JSON 兼容字典。
    """
    observed_at = now or utc_now()
    # 总耗时从创建开始计算，排队阶段也属于用户实际等待时间。
    elapsed_end = task.finished_at or observed_at
    elapsed_seconds = max(0.0, (elapsed_end - task.created_at).total_seconds())
    if task.total_samples > 0:
        progress_percent = round(task.completed_samples / task.total_samples * 100, 1)
    else:
        progress_percent = 0.0

    # 结果摘要单独组装，避免列表为了展示得分而携带失败样例或原始正文。
    result_summary = None
    if task.average_score is not None:
        result_summary = {
            "benchmark": task.benchmark,
            "total_samples": task.total_samples,
            "passed_samples": task.passed_samples,
            "average_score": task.average_score,
        }
    return {
        "id": task.id,
        "status": task.status,
        "evaluation_type": task.request.evaluation_type,
        "agent_framework": task.request.agent_framework,
        "dataset": task.request.dataset,
        "suite_id": task.request.suite_id,
        "model": task.request.model,
        "adapter": task.request.adapter,
        "progress": {
            "completed_samples": task.completed_samples,
            "total_samples": task.total_samples,
            "percent": progress_percent,
        },
        "timing": {
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "elapsed_seconds": round(elapsed_seconds, 1),
        },
        "resources": {
            "cpu": {
                "current_percent": task.cpu_percent,
                "peak_percent": task.peak_cpu_percent,
            },
            "memory": {
                "current_bytes": task.memory_bytes,
                "peak_bytes": task.peak_memory_bytes,
            },
            "gpu": {
                "supported": task.gpu_supported,
                "current_percent": task.gpu_percent,
                "peak_percent": task.peak_gpu_percent,
                "current_memory_bytes": task.gpu_memory_bytes,
                "peak_memory_bytes": task.peak_gpu_memory_bytes,
            },
        },
        "result_summary": result_summary,
        "error_message": task.error_message,
    }


def task_detail(
    task: EvaluationTask,
    *,
    nodes: list[EvaluationNode] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """构建包含请求配置、完整结果和工作流节点的任务详情响应。

    Args:
        task: 从仓储读取的任务状态快照。
        nodes: 当前任务的工作流节点；缺省时返回空节点列表。
        now: 运行态耗时计算基准；测试可传入固定 UTC 时间。

    Returns:
        包含请求、结果和节点诊断信息的 JSON 兼容字典。
    """
    detail = task_summary(task, now=now)
    # 请求和结果只在详情层披露，保持列表轮询响应稳定且轻量。
    detail["request"] = asdict(task.request)
    result = task.result
    # 套件汇总完成但任务结果列尚未落盘时，直接复用最终节点的持久化输出。
    if result is None:
        finalizer = next(
            (node for node in nodes or [] if node.kind == "workflow_finalize" and node.output),
            None,
        )
        if finalizer is not None:
            result = finalizer.output
    detail["result"] = result
    detail["nodes"] = [node_summary(node, now=now) for node in nodes or []]
    return detail


def node_summary(node: EvaluationNode, *, now: datetime | None = None) -> dict[str, object]:
    """构建任务详情中用于快速扫描的节点最新状态。"""
    observed_at = now or utc_now()
    elapsed_ms = node.elapsed_ms
    if node.status == "running" and node.attempt_started_at is not None:
        elapsed_ms += max(
            0,
            int((observed_at - node.attempt_started_at).total_seconds() * 1000),
        )
    if node.total_samples > 0:
        percent = round(node.completed_samples / node.total_samples * 100, 1)
    else:
        percent = 0.0
    return {
        "id": node.id,
        "task_id": node.task_id,
        "node_key": node.node_key,
        "kind": node.kind,
        "depends_on": list(node.depends_on),
        "status": node.status,
        "attempt": {
            "count": node.attempt_count,
            "max": node.max_attempts,
        },
        "progress": {
            "completed_samples": node.completed_samples,
            "total_samples": node.total_samples,
            "percent": percent,
        },
        "timing": {
            "created_at": node.created_at.isoformat(),
            "started_at": node.started_at.isoformat() if node.started_at else None,
            "finished_at": node.finished_at.isoformat() if node.finished_at else None,
            "elapsed_ms": elapsed_ms,
        },
        "error": (
            {"type": node.error_type, "message": node.error_message}
            if node.error_type or node.error_message
            else None
        ),
    }


def node_detail(
    node: EvaluationNode,
    *,
    events: list[EvaluationNodeEvent],
    now: datetime | None = None,
) -> dict[str, object]:
    """构建节点输入、检查点、输出与追加式审计事件详情。"""
    detail = node_summary(node, now=now)
    detail.update(
        {
            "input": node.input,
            "checkpoint": node.checkpoint,
            "output": node.output,
            "events": [node_event(item) for item in events],
        }
    )
    return detail


def node_event(event: EvaluationNodeEvent) -> dict[str, object]:
    """把不可变节点事件转换为稳定 JSON 结构。"""
    return {
        "id": event.id,
        "event_type": event.event_type,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "attempt": event.attempt,
        "actor": event.actor,
        "message": event.message,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def sample_checkpoint(sample: EvaluationSampleCheckpoint) -> dict[str, object]:
    """把一个样本最新检查点转换为可调试但不加工内容的响应。"""
    return {
        "task_id": sample.task_id,
        "node_id": sample.node_id,
        "sample_key": sample.sample_key,
        "sample_index": sample.sample_index,
        "status": sample.status,
        "attempt_count": sample.attempt_count,
        "input": sample.input,
        "result": sample.result,
        "last_error": sample.last_error,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
        "updated_at": sample.updated_at.isoformat() if sample.updated_at else None,
        "finished_at": sample.finished_at.isoformat() if sample.finished_at else None,
    }


def sample_page(page: EvaluationSamplePage) -> dict[str, object]:
    """构建带稳定游标的一页节点样本响应。"""
    return {
        "samples": [sample_checkpoint(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }
