from evalhub.adapters import StaticMappingAdapter
from evalhub.domain import BenchmarkRecord, EvaluationJob, EvaluationSample
from evalhub.domain.enums import JobStatus
from evalhub.engine import EvaluationRunner
from evalhub.evaluators import ExactMatchEvaluator


def test_runner_generates_sample_results_and_report() -> None:
    samples = [
        EvaluationSample(id="s1", input="1 + 1", reference="2"),
        EvaluationSample(id="s2", input="2 + 2", reference="4"),
    ]
    adapter = StaticMappingAdapter({"1 + 1": "2", "2 + 2": "wrong"})
    runner = EvaluationRunner(adapter, ExactMatchEvaluator())
    job = EvaluationJob(model_id="model_1", benchmark_id="benchmark_1")
    benchmark = BenchmarkRecord(
        id="benchmark_1",
        name="math-mini",
        dataset_id="dataset_1",
        evaluator_type="exact_match",
    )

    results, report = runner.run(job=job, benchmark=benchmark, samples=samples)

    assert job.status == JobStatus.SUCCESS
    assert len(results) == 2
    assert report.total_samples == 2
    assert report.passed_samples == 1
    assert report.average_score == 0.5
    assert report.failed_sample_ids == ["s2"]
