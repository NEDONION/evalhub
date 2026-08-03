from evalhub.adapters.base import ModelAdapter
from evalhub.domain.entities import (
    BenchmarkRecord,
    EvaluationJob,
    EvaluationReport,
    EvaluationSample,
    EvaluationSampleResult,
)
from evalhub.engine.reports import build_report
from evalhub.evaluators.base import Evaluator


class EvaluationRunner:
    def __init__(self, model_adapter: ModelAdapter, evaluator: Evaluator) -> None:
        self.model_adapter = model_adapter
        self.evaluator = evaluator

    def run(
        self,
        *,
        job: EvaluationJob,
        benchmark: BenchmarkRecord,
        samples: list[EvaluationSample],
    ) -> tuple[list[EvaluationSampleResult], EvaluationReport]:
        job.mark_running()
        results: list[EvaluationSampleResult] = []

        try:
            runtime_config = {**benchmark.config, **job.runtime_config}
            for sample in samples:
                prediction = self.model_adapter.generate(sample.input, **runtime_config)
                metric = self.evaluator.evaluate(
                    prediction,
                    sample.reference,
                    input_text=sample.input,
                    metadata=sample.metadata,
                )
                results.append(
                    EvaluationSampleResult(
                        job_id=job.id,
                        sample_id=sample.id,
                        input=sample.input,
                        prediction=prediction,
                        reference=sample.reference,
                        metric=metric.metric,
                        score=metric.score,
                        reason=metric.reason,
                    )
                )

            report = build_report(job.id, results)
            job.mark_success()
            return results, report
        except Exception as exc:
            job.mark_failed(str(exc))
            raise
