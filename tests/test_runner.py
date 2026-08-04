"""验证同步评测执行器的样本结果、任务状态和报告聚合。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import evalhub.cli as cli_module
from evalhub.adapters import StaticMappingAdapter
from evalhub.domain import (
    BenchmarkRecord,
    EvaluationJob,
    EvaluationSample,
    EvaluationSampleResult,
    MetricResult,
)
from evalhub.domain.enums import JobStatus
from evalhub.engine import EvaluationRunner
from evalhub.evaluators import ExactMatchEvaluator


class RecordingAdapter(StaticMappingAdapter):
    """记录实际送入模型边界的提示词，便于证明展示翻译没有混入调用。"""

    def __init__(self, response: str) -> None:
        """配置固定响应并初始化调用记录。

        Args:
            response: 任意提示词都返回的确定性模型文本。
        """
        super().__init__({}, default_response=response)
        self.inputs: list[str] = []

    def generate(self, prompt: str, **kwargs: object) -> str:
        """记录英文提示并返回固定响应。

        Args:
            prompt: Runner 实际提交给模型的文本。
            **kwargs: Benchmark 传入的确定性生成参数。

        Returns:
            构造测试适配器时配置的固定响应。
        """
        self.inputs.append(prompt)
        return super().generate(prompt, **kwargs)


class RecordingEvaluator(ExactMatchEvaluator):
    """记录评分器收到的元数据，验证展示字段不会越过评分边界。"""

    def __init__(self) -> None:
        """初始化尚未收到评分请求的元数据记录。"""
        super().__init__()
        self.metadata: dict[str, object] | None = None

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """记录评分元数据后复用真实精确匹配逻辑。

        Args:
            prediction: 固定适配器生成的模型文本。
            reference: 样本提供的官方参考答案。
            input_text: Runner 传入的官方英文题面。
            metadata: 已移除纯展示翻译、仍保留评分字段的元数据。

        Returns:
            真实精确匹配评分结果。
        """
        self.metadata = metadata
        return super().evaluate(
            prediction,
            reference,
            input_text=input_text,
            metadata=metadata,
        )


def test_sample_result_keeps_legacy_optional_positional_arguments() -> None:
    """旧调用方按位置传入 id 和 created_at 时不得被新增 metadata 字段截断。"""
    created_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)

    result = EvaluationSampleResult(
        "job_1",
        "sample_1",
        "input",
        "prediction",
        "reference",
        "exact_match",
        1.0,
        None,
        "result_legacy",
        created_at,
    )

    assert result.id == "result_legacy"
    assert result.created_at == created_at
    assert result.metadata == {}


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
    assert results[0].metadata == {}


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


def test_runner_preserves_display_metadata_without_sending_it_to_model_or_evaluator() -> None:
    """结果应保留完整展示溯源，但模型和评分器只能接收各自需要的英文数据。"""
    sample = EvaluationSample(
        id="s1",
        input="English only",
        reference="A",
        metadata={
            "input_zh": "仅供展示",
            "reference_zh": "答案展示",
            "source_key": "subject:1",
        },
    )
    adapter = RecordingAdapter("A")
    evaluator = RecordingEvaluator()
    runner = EvaluationRunner(adapter, evaluator)
    job = EvaluationJob(model_id="model_1", benchmark_id="benchmark_1")
    benchmark = BenchmarkRecord(
        id="benchmark_1",
        name="metadata-mini",
        dataset_id="dataset_1",
        evaluator_type="exact_match",
    )

    results, _ = runner.run(job=job, benchmark=benchmark, samples=[sample])

    assert adapter.inputs == ["English only"]
    assert evaluator.metadata == {"source_key": "subject:1"}
    assert results[0].metadata == sample.metadata


def test_real_benchmark_failed_example_keeps_sample_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同步入口的失败示例应沿用领域结果元数据，供后续展示来源与翻译。"""
    sample = EvaluationSample(
        id="ifeval-1",
        input="Return text without a comma",
        reference="",
        metadata={
            "input_zh": "请返回不含逗号的文本",
            "source_key": "32",
            "instruction_id_list": ["punctuation:no_comma"],
            "kwargs": [{}],
        },
    )
    monkeypatch.setattr(cli_module, "prepare_dataset", lambda dataset: None)
    monkeypatch.setattr(cli_module, "load_samples", lambda *args, **kwargs: [sample])
    monkeypatch.setattr(
        cli_module,
        "get_dataset_spec",
        lambda dataset: SimpleNamespace(
            name=dataset,
            display_name="IFEval fixture",
            local_path="fixture.jsonl",
            evaluator_type="ifeval_strict",
        ),
    )

    result = cli_module.run_real_benchmark(
        dataset="ifeval-fixture",
        adapter_type="oracle",
        model="oracle",
        base_url="http://127.0.0.1:11434",
        limit=None,
        subject="",
    )

    assert result["failed_examples"][0]["metadata"] == sample.metadata
