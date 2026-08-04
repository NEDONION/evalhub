"""验证模型历史成绩按可比范围聚合、排名并识别刷新纪录。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evalhub.tasks import (
    EvaluationTask,
    EvaluationType,
    SQLiteTaskRepository,
    TaskRequest,
    TaskStatus,
)
from evalhub.tasks.performance import build_model_performance

BASE_TIME = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)


def performance_task(
    task_id: str,
    *,
    model: str,
    score: float,
    minute: int,
    dataset: str = "gsm8k",
    suite_id: str | None = None,
    evaluation_type: EvaluationType = "model",
    status: TaskStatus = "success",
    completed_samples: int = 10,
    total_samples: int = 10,
    comparison_fingerprint: str | None = None,
) -> EvaluationTask:
    """构造具备稳定时间、范围和得分的历史任务。

    Args:
        task_id: 历史点使用的稳定任务标识。
        model: 参与比较的模型名称。
        score: 已持久化的平均分，使用 0 到 1 范围。
        minute: 相对基准时间的完成分钟数，用于验证时间顺序。
        dataset: 单项 Benchmark 的稳定标识。
        suite_id: Suite 稳定标识；提供时不再按单项 Benchmark 分组。
        evaluation_type: 模型或 Agent 任务类型，用于验证排除逻辑。
        status: 持久化任务终态，用于覆盖不完整但错误标成功的回归场景。
        completed_samples: 已形成评分结果的持久化样本数。
        total_samples: 任务声明的持久化样本总数。
        comparison_fingerprint: 完整 Suite 的来源、清单、提示和生成协议摘要。

    Returns:
        不依赖数据库或墙上时钟的完整任务快照。
    """
    finished_at = BASE_TIME + timedelta(minutes=minute)
    request = TaskRequest(
        dataset=dataset,
        adapter="oracle",
        model=model,
        base_url="http://127.0.0.1:11434",
        sample_mode="all",
        subject="abstract_algebra",
        limit=None,
        evaluation_type=evaluation_type,
        agent_framework="pi" if evaluation_type == "agent" else None,
        suite_id=suite_id,
    )
    fingerprint = comparison_fingerprint
    if fingerprint is None and suite_id == "evalhub-hexagon-v1":
        fingerprint = "hexagon-v1-fixture"
    # 聚合只允许读取轻量摘要；result 明确为空可捕获意外依赖完整结果正文。
    return EvaluationTask(
        id=task_id,
        request=request,
        status=status,
        completed_samples=completed_samples,
        total_samples=total_samples,
        created_at=finished_at - timedelta(minutes=1),
        updated_at=finished_at,
        started_at=finished_at - timedelta(minutes=1),
        finished_at=finished_at,
        benchmark="行业核心套件" if suite_id else dataset.upper(),
        passed_samples=round(score * 10),
        average_score=score,
        comparison_fingerprint=fingerprint,
        result=None,
    )


def test_performance_isolates_scopes_agents_and_ranks_historical_bests() -> None:
    """不同范围和 Agent 分数不得污染模型最佳成绩排行榜。"""
    tasks = [
        performance_task("qwen-first", model="qwen", score=0.6, minute=1),
        performance_task("qwen-record", model="qwen", score=0.8, minute=2),
        performance_task("qwen-latest", model="qwen", score=0.75, minute=3),
        performance_task("llama-best", model="llama", score=0.7, minute=4),
        performance_task(
            "failed-score",
            model="broken",
            score=0.99,
            minute=5,
            status="failed",
        ),
        performance_task("mmlu-high", model="other", score=0.99, minute=6, dataset="mmlu"),
        performance_task(
            "agent-high",
            model="agent-model",
            score=1.0,
            minute=7,
            dataset="coding_mini",
            evaluation_type="agent",
        ),
    ]

    report = build_model_performance(tasks, "benchmark:gsm8k")

    assert report.selected_scope is not None
    assert report.selected_scope.key == "benchmark:gsm8k"
    assert [item.model for item in report.models] == ["qwen", "llama"]
    assert report.models[0].best_score == 0.8
    assert report.models[0].latest_score == 0.75
    assert [point.improvement for point in report.models[0].history] == [None, 0.2, None]
    assert [point.is_record for point in report.models[0].history] == [False, True, False]
    assert report.record is not None
    assert (report.record.model, report.record.task_id) == ("qwen", "qwen-record")


def test_performance_defaults_to_most_used_scope_and_keeps_suites_separate() -> None:
    """默认范围应选择有效运行最多的一组，并让 Suite 与单项任务严格分离。"""
    tasks = [
        performance_task("suite-a", model="qwen", score=0.55, minute=1, suite_id="core-v1"),
        performance_task("suite-b", model="llama", score=0.65, minute=2, suite_id="core-v1"),
        performance_task("single", model="qwen", score=0.95, minute=3),
    ]

    report = build_model_performance(tasks, None)

    assert report.selected_scope is not None
    assert report.selected_scope.key == "suite:core-v1"
    assert report.selected_scope.run_count == 2
    assert [item.model for item in report.models] == ["llama", "qwen"]
    assert {scope.key for scope in report.scopes} == {"suite:core-v1", "benchmark:gsm8k"}


def test_performance_rejects_unknown_scope_but_allows_empty_history() -> None:
    """未知筛选应明确失败，而无历史的默认查询应返回稳定空报告。"""
    empty = build_model_performance([], None)

    assert empty.selected_scope is None
    assert empty.models == ()
    assert empty.record is None
    with pytest.raises(ValueError, match="unknown model performance scope"):
        build_model_performance([], "benchmark:missing")


def test_performance_ties_are_deterministic_and_equal_scores_do_not_set_records() -> None:
    """同分模型按名称稳定排序，同模型追平历史最佳时不得误报刷新纪录。"""
    tasks = [
        performance_task("qwen-first", model="qwen", score=0.8, minute=1),
        performance_task("qwen-equal", model="qwen", score=0.8, minute=2),
        performance_task("zeta-first", model="zeta", score=0.7, minute=3),
        performance_task("alpha-first", model="alpha", score=0.7, minute=4),
    ]

    report = build_model_performance(tasks, "benchmark:gsm8k")

    assert [item.model for item in report.models] == ["qwen", "alpha", "zeta"]
    assert [point.is_record for point in report.models[0].history] == [False, False]
    assert report.record is None


def test_performance_excludes_numeric_partial_hexagon_suite_even_if_marked_success() -> None:
    """Hexagon 仅接收当前 30 题完整任务，旧 60 题和部分任务都不能进入排行榜。"""
    tasks = [
        performance_task(
            "old-revision-hexagon",
            model="old-revision",
            score=1.0,
            minute=0,
            suite_id="evalhub-hexagon-v1",
            completed_samples=60,
            total_samples=60,
            comparison_fingerprint="hexagon-v0-fixture",
        ),
        performance_task(
            "partial-hexagon",
            model="partial",
            score=1.0,
            minute=1,
            suite_id="evalhub-hexagon-v1",
            status="success",
            completed_samples=29,
            total_samples=30,
        ),
        performance_task(
            "complete-hexagon",
            model="complete",
            score=0.8,
            minute=2,
            suite_id="evalhub-hexagon-v1",
            status="success",
            completed_samples=30,
            total_samples=30,
        ),
    ]

    report = build_model_performance(tasks, "suite:evalhub-hexagon-v1")

    assert report.selected_scope is not None
    assert report.selected_scope.run_count == 1
    assert [model.model for model in report.models] == ["complete"]


def test_repository_lists_only_scored_summaries_without_result_body(tmp_path: Path) -> None:
    """成绩查询只应返回有分摘要，且不能加载完整结果正文。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    scored_request = performance_task("source", model="qwen", score=0.8, minute=1).request
    scored = repository.create(scored_request)
    repository.mark_running(scored.id)
    repository.mark_success(
        scored.id,
        {
            "job_id": scored.id,
            "benchmark": "GSM8K",
            "total_samples": 10,
            "passed_samples": 8,
            "average_score": 0.8,
            "comparison_fingerprint": "protocol-fixture",
        },
    )
    repository.create(performance_task("pending", model="llama", score=0.9, minute=2).request)

    listed = repository.list_scored()

    assert [task.id for task in listed] == [scored.id]
    assert listed[0].average_score == 0.8
    assert listed[0].comparison_fingerprint == "protocol-fixture"
    assert listed[0].result is None
