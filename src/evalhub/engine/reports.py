"""把样本级评测结果聚合为任务级统计报告。"""

from evalhub.domain.entities import EvaluationReport, EvaluationSampleResult


def build_report(job_id: str, results: list[EvaluationSampleResult]) -> EvaluationReport:
    """根据一组同指标样本结果构建任务汇总报告。

    Args:
        job_id: 结果所属评测任务的稳定标识。
        results: 已完成评分的样本级结果；空列表会生成零样本报告。

    Returns:
        包含总数、通过数、均分和失败样本标识的不可变报告。
    """
    # 空任务仍返回结构完整的报告，避免调用方额外处理 ``None`` 分支。
    if not results:
        return EvaluationReport(
            job_id=job_id,
            metric="unknown",
            total_samples=0,
            passed_samples=0,
            average_score=0.0,
            failed_sample_ids=[],
        )

    # Runner 在一次任务中使用同一评测器，因此首条结果即可确定报告指标名。
    metric = results[0].metric
    total_samples = len(results)
    passed_samples = sum(1 for result in results if result.score >= 1.0)
    average_score = sum(result.score for result in results) / total_samples

    # 单独保留未满分样本标识，为报告详情和失败分析提供快速索引。
    failed_sample_ids = [result.sample_id for result in results if result.score < 1.0]

    # 将计算后的标量收敛为领域报告，保证上层只依赖稳定数据结构。
    return EvaluationReport(
        job_id=job_id,
        metric=metric,
        total_samples=total_samples,
        passed_samples=passed_samples,
        average_score=average_score,
        failed_sample_ids=failed_sample_ids,
    )
