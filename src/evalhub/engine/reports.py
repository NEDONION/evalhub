from evalhub.domain.entities import EvaluationReport, EvaluationSampleResult


def build_report(job_id: str, results: list[EvaluationSampleResult]) -> EvaluationReport:
    if not results:
        return EvaluationReport(
            job_id=job_id,
            metric="unknown",
            total_samples=0,
            passed_samples=0,
            average_score=0.0,
            failed_sample_ids=[],
        )

    metric = results[0].metric
    total_samples = len(results)
    passed_samples = sum(1 for result in results if result.score >= 1.0)
    average_score = sum(result.score for result in results) / total_samples
    failed_sample_ids = [result.sample_id for result in results if result.score < 1.0]

    return EvaluationReport(
        job_id=job_id,
        metric=metric,
        total_samples=total_samples,
        passed_samples=passed_samples,
        average_score=average_score,
        failed_sample_ids=failed_sample_ids,
    )
