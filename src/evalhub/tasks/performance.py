"""按可比评测范围聚合模型历史成绩、排行榜和刷新纪录。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from evalhub.tasks.models import EvaluationTask

PerformanceScopeKind = Literal["benchmark", "suite"]
_HEXAGON_SUITE_ID = "evalhub-hexagon-v1"
_HEXAGON_SAMPLE_COUNT = 30


@dataclass(frozen=True)
class PerformanceScope:
    """描述一个可独立比较模型成绩的 Benchmark 或 Suite 范围。"""

    key: str
    kind: PerformanceScopeKind
    identifier: str
    label: str
    run_count: int


@dataclass(frozen=True)
class PerformancePoint:
    """描述模型在所选范围内一次有效评测形成的历史成绩点。"""

    scope_key: str
    task_id: str
    model: str
    score: float
    completed_at: datetime
    is_record: bool
    improvement: float | None


@dataclass(frozen=True)
class ModelPerformance:
    """汇总一个模型在所选范围内的历史最佳、最新成绩和完整趋势。"""

    model: str
    best_score: float
    latest_score: float
    run_count: int
    best_task_id: str
    best_at: datetime
    latest_at: datetime
    history: tuple[PerformancePoint, ...]


@dataclass(frozen=True)
class ModelPerformanceReport:
    """保存可选比较范围、当前排行榜和最近刷新纪录。"""

    scopes: tuple[PerformanceScope, ...]
    selected_scope: PerformanceScope | None
    models: tuple[ModelPerformance, ...]
    record: PerformancePoint | None


def build_model_performance(
    tasks: list[EvaluationTask],
    scope: str | None,
) -> ModelPerformanceReport:
    """从轻量任务摘要构建一个按可比范围隔离的模型成绩报告。

    Args:
        tasks: 包含请求类型、模型、范围、得分和完成时间的任务摘要。
        scope: `benchmark:<id>` 或 `suite:<id>`；为空时选择有效运行数最多的范围。

    Returns:
        范围列表、当前排行榜、模型历史点和最近刷新纪录。

    Raises:
        ValueError: 请求的范围没有任何可比较模型成绩。
    """
    # 所有排行榜只接受成功模型任务；Hexagon 再核对固定题数和协议指纹。
    comparable = [task for task in tasks if _is_comparable(task)]
    grouped = _group_by_scope(comparable)
    scopes = tuple(_build_scope(key, items) for key, items in grouped.items())
    scopes = tuple(sorted(scopes, key=lambda item: (-item.run_count, item.key)))

    # 默认选择运行次数最多的范围；显式范围不存在时返回可诊断客户端错误。
    selected_key = scope or (scopes[0].key if scopes else None)
    selected_scope = next((item for item in scopes if item.key == selected_key), None)
    if selected_key is not None and selected_scope is None:
        raise ValueError(f"unknown model performance scope: {selected_key}")
    if selected_scope is None:
        return ModelPerformanceReport(scopes, None, (), None)

    # 同一范围内再按模型分组，避免最佳分和趋势在模型之间相互污染。
    model_groups: dict[str, list[EvaluationTask]] = {}
    for task in grouped[selected_scope.key]:
        model_groups.setdefault(task.request.model, []).append(task)
    models = tuple(
        _build_model(model, items, selected_scope.key) for model, items in model_groups.items()
    )
    models = tuple(
        sorted(models, key=lambda item: (-item.best_score, -item.latest_score, item.model))
    )
    # 最新纪录从已经排序的所有模型历史中派生，不受排行榜名次影响。
    record = _latest_improved_record(models)
    return ModelPerformanceReport(scopes, selected_scope, models, record)


def _is_comparable(task: EvaluationTask) -> bool:
    """判断任务是否具备进入模型历史比较的最小持久化事实。

    Args:
        task: 不含大型结果正文、但包含请求、终态和样本计数的任务摘要。

    Returns:
        非成功、Agent 或无分任务返回 ``False``；Hexagon 还必须是 30 题全量运行。
    """
    if (
        task.status != "success"
        or task.request.evaluation_type != "model"
        or task.average_score is None
    ):
        return False
    if task.request.suite_id != _HEXAGON_SUITE_ID:
        return True
    # 仅靠 success 不足以识别旧数据中的部分套件，必须同时核对模式和持久化计数。
    return (
        task.status == "success"
        and task.request.sample_mode == "all"
        and task.completed_samples == _HEXAGON_SAMPLE_COUNT
        and task.total_samples == _HEXAGON_SAMPLE_COUNT
        and task.comparison_fingerprint is not None
    )


def _group_by_scope(tasks: list[EvaluationTask]) -> dict[str, list[EvaluationTask]]:
    """按 Suite 或单 Benchmark 稳定键对有效模型任务分组。

    Args:
        tasks: 已排除 Agent 和无分记录的模型任务。

    Returns:
        稳定范围键到该范围全部历史任务的映射。
    """
    grouped: dict[str, list[EvaluationTask]] = {}
    for task in tasks:
        key = _scope_key(task)
        grouped.setdefault(key, []).append(task)
    hexagon_key = f"suite:{_HEXAGON_SUITE_ID}"
    if hexagon_key in grouped:
        grouped[hexagon_key] = _latest_protocol_tasks(grouped[hexagon_key])
    return grouped


def _latest_protocol_tasks(tasks: list[EvaluationTask]) -> list[EvaluationTask]:
    """只保留与最近一次完整 Hexagon 运行具有相同协议指纹的成绩。

    Args:
        tasks: 已通过完整性检查、但可能来自多个历史协议 revision 的 Suite 任务。

    Returns:
        与最近完成任务协议完全一致的可比较任务，公开 Suite 范围键保持不变。
    """
    latest = max(tasks, key=_task_time_key)
    fingerprint = latest.comparison_fingerprint
    return [task for task in tasks if task.comparison_fingerprint == fingerprint]


def _scope_key(task: EvaluationTask) -> str:
    """返回任务唯一且不会混合 Suite 与 Benchmark 的可比范围键。

    Args:
        task: 包含可选 Suite 标识和数据集标识的模型任务。

    Returns:
        `suite:<id>` 或 `benchmark:<dataset>` 格式的稳定键。
    """
    if task.request.suite_id:
        return f"suite:{task.request.suite_id}"
    return f"benchmark:{task.request.dataset}"


def _build_scope(key: str, tasks: list[EvaluationTask]) -> PerformanceScope:
    """从同组任务推导范围类型、标识、显示名称和有效运行数。

    Args:
        key: 聚合阶段生成的稳定范围键。
        tasks: 归属于该范围的全部有效模型任务。

    Returns:
        包含用户可见名称和运行次数的不可变范围摘要。
    """
    kind_text, identifier = key.split(":", 1)
    kind: PerformanceScopeKind = "suite" if kind_text == "suite" else "benchmark"
    # 最近一次任务的持久化名称最贴近当前 Registry，缺失时仍保留稳定标识。
    latest = max(tasks, key=_task_time_key)
    label = latest.benchmark or identifier
    return PerformanceScope(key, kind, identifier, label, len(tasks))


def _build_model(
    model: str,
    tasks: list[EvaluationTask],
    scope_key: str,
) -> ModelPerformance:
    """按时间生成一个模型的纪录点，并计算最佳与最新成绩。

    Args:
        model: 当前聚合的模型名称。
        tasks: 同模型、同范围的全部有效历史任务。
        scope_key: 每个历史点需要携带的稳定比较范围键。

    Returns:
        含历史最佳、最新成绩和有序趋势点的模型表现摘要。
    """
    ordered = sorted(tasks, key=_task_time_key)
    points: list[PerformancePoint] = []
    best_so_far: float | None = None
    for task in ordered:
        score = float(task.average_score or 0.0)
        improved = best_so_far is not None and score > best_so_far
        improvement = (
            round(score - best_so_far, 6) if improved and best_so_far is not None else None
        )
        is_record = improved
        new_best = best_so_far is None or improved
        # 只在严格变好时推进纪录，并保留回落点供趋势图如实展示。
        if new_best:
            best_so_far = score
        points.append(
            PerformancePoint(
                scope_key=scope_key,
                task_id=task.id,
                model=model,
                score=score,
                completed_at=_task_completed_at(task),
                is_record=is_record,
                improvement=improvement,
            )
        )

    # 同分最佳取最近一次，便于从榜单跳转到最新的完整任务诊断。
    best = max(points, key=lambda item: (item.score, item.completed_at, item.task_id))
    latest = points[-1]
    return ModelPerformance(
        model=model,
        best_score=best.score,
        latest_score=latest.score,
        run_count=len(points),
        best_task_id=best.task_id,
        best_at=best.completed_at,
        latest_at=latest.completed_at,
        history=tuple(points),
    )


def _latest_improved_record(models: tuple[ModelPerformance, ...]) -> PerformancePoint | None:
    """返回所有模型中最近一次真正超过既有最好成绩的历史点。

    Args:
        models: 当前范围内已经生成历史点的模型表现集合。

    Returns:
        最近的严格提分点；只有初始成绩或没有提分时返回空值。
    """
    improved = [
        point for model in models for point in model.history if point.improvement is not None
    ]
    if not improved:
        return None
    return max(improved, key=lambda item: (item.completed_at, item.task_id))


def _task_completed_at(task: EvaluationTask) -> datetime:
    """返回成绩点使用的终态时间，并兼容缺少完成时间的旧记录。

    Args:
        task: 已经产生有效分数的持久化任务摘要。

    Returns:
        优先使用完成时间，否则回退到最后更新时间的带时区时间戳。
    """
    return task.finished_at or task.updated_at


def _task_time_key(task: EvaluationTask) -> tuple[datetime, str]:
    """为历史点和范围名称选择提供确定性的排序键。

    Args:
        task: 需要进入稳定时间顺序的持久化任务摘要。

    Returns:
        由有效完成时间和任务标识组成的可比较元组。
    """
    return (_task_completed_at(task), task.id)
