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
    # 均分和失败标识共同验证报告没有只统计数量而丢失样本级错误定位。
    assert report.average_score == 0.5
    assert report.failed_sample_ids == ["s2"]


def test_runner_reports_progress_after_each_scored_sample() -> None:
    """Runner 应在每条样本形成结果后按顺序报告真实完成数量。"""
    # 两条静态样本让测试能精确区分首次和最终进度，且完全不依赖外部模型。
    samples = [
        EvaluationSample(id="s1", input="1 + 1", reference="2"),
        EvaluationSample(id="s2", input="2 + 2", reference="4"),
    ]
    adapter = StaticMappingAdapter({"1 + 1": "2", "2 + 2": "4"})
    runner = EvaluationRunner(adapter, ExactMatchEvaluator())

    # 任务与 Benchmark 只提供 Runner 所需的稳定关联，回调列表记录公开可观察行为。
    job = EvaluationJob(model_id="model_1", benchmark_id="benchmark_1")
    benchmark = BenchmarkRecord(
        id="benchmark_1",
        name="math-mini",
        dataset_id="dataset_1",
        evaluator_type="exact_match",
    )
    updates: list[tuple[int, int]] = []

    # 每次回调应发生在对应结果已经生成之后，最终更新必须等于样本总数。
    results, _ = runner.run(
        job=job,
        benchmark=benchmark,
        samples=samples,
        on_progress=lambda completed, total: updates.append((completed, total)),
    )

    assert len(results) == 2
    assert updates == [(1, 2), (2, 2)]


def test_runner_skips_completed_samples_and_reports_new_results() -> None:
    """恢复执行应跳过成功样本，并只为新结果触发持久化回调。"""
    samples = [
        EvaluationSample(id="s1", input="1 + 1", reference="2"),
        EvaluationSample(id="s2", input="2 + 2", reference="4"),
    ]
    adapter = StaticMappingAdapter({"1 + 1": "2", "2 + 2": "4"})
    runner = EvaluationRunner(adapter, ExactMatchEvaluator())
    job = EvaluationJob(model_id="model_1", benchmark_id="benchmark_1")
    benchmark = BenchmarkRecord(
        id="benchmark_1",
        name="math-mini",
        dataset_id="dataset_1",
        evaluator_type="exact_match",
    )
    emitted: list[tuple[str, int, int]] = []
    progress: list[tuple[int, int]] = []

    results, _ = runner.run(
        job=job,
        benchmark=benchmark,
        samples=samples,
        skip_sample_ids={"s1"},
        on_sample_result=lambda result, completed, total: emitted.append(
            (result.sample_id, completed, total)
        ),
        on_progress=lambda completed, total: progress.append((completed, total)),
    )

    assert [result.sample_id for result in results] == ["s2"]
    assert emitted == [("s2", 2, 2)]
    assert progress == [(2, 2)]
