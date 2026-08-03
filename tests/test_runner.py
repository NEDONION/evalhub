"""验证同步评测执行器的样本结果、任务状态和报告聚合。"""

from evalhub.adapters import StaticMappingAdapter
from evalhub.domain import BenchmarkRecord, EvaluationJob, EvaluationSample
from evalhub.domain.enums import JobStatus
from evalhub.engine import EvaluationRunner
from evalhub.evaluators import ExactMatchEvaluator


def test_runner_generates_sample_results_and_report() -> None:
    """Runner 应按样本顺序评分并生成包含失败标识的正确汇总。"""
    # 两条数学样本分别覆盖满分与错误预测，便于验证二值聚合的所有关键字段。
    samples = [
        EvaluationSample(id="s1", input="1 + 1", reference="2"),
        EvaluationSample(id="s2", input="2 + 2", reference="4"),
    ]
    # 静态适配器让第一条命中、第二条失败，从而无需任何外部模型服务。
    adapter = StaticMappingAdapter({"1 + 1": "2", "2 + 2": "wrong"})
    runner = EvaluationRunner(adapter, ExactMatchEvaluator())
    # 任务和 Benchmark 使用匹配标识，模拟真实 Registry 关联但保持测试最小化。
    job = EvaluationJob(model_id="model_1", benchmark_id="benchmark_1")
    benchmark = BenchmarkRecord(
        id="benchmark_1",
        name="math-mini",
        dataset_id="dataset_1",
        evaluator_type="exact_match",
    )

    # 通过公开执行入口同时获得样本结果与报告，不断言内部调用顺序。
    results, report = runner.run(job=job, benchmark=benchmark, samples=samples)

    # 成功任务应包含两条结果、一个通过样本、0.5 均分和第二条失败标识。
    assert job.status == JobStatus.SUCCESS
    assert len(results) == 2
    assert report.total_samples == 2
    assert report.passed_samples == 1
    assert report.average_score == 0.5
    assert report.failed_sample_ids == ["s2"]
