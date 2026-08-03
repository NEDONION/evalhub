"""编排模型推理、样本评分、任务状态和报告生成的同步评测流程。"""

from collections.abc import Callable

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

ProgressCallback = Callable[[int, int], None]
SampleResultCallback = Callable[[EvaluationSampleResult, int, int], None]


class EvaluationRunner:
    """使用可替换的模型适配器和评测器执行一批评测样本。"""

    def __init__(self, model_adapter: ModelAdapter, evaluator: Evaluator) -> None:
        """注入本次执行使用的模型调用边界与评分策略。

        Args:
            model_adapter: 把文本输入转换为模型预测的适配器。
            evaluator: 把预测与参考答案转换为指标结果的评测器。
        """
        # Runner 只依赖抽象接口，使具体模型服务和评分算法可以独立替换。
        self.model_adapter = model_adapter
        self.evaluator = evaluator

    def run(
        self,
        *,
        job: EvaluationJob,
        benchmark: BenchmarkRecord,
        samples: list[EvaluationSample],
        on_progress: ProgressCallback | None = None,
        skip_sample_ids: set[str] | frozenset[str] = frozenset(),
        on_sample_result: SampleResultCallback | None = None,
    ) -> tuple[list[EvaluationSampleResult], EvaluationReport]:
        """同步执行全部样本并生成样本结果与任务报告。

        Args:
            job: 需要更新生命周期状态的评测任务。
            benchmark: 提供默认评测运行参数的 Benchmark。
            samples: 按输入顺序执行的领域样本列表。
            on_progress: 每条样本完成后接收已完成数量和总数量的可选回调。
            skip_sample_ids: 已经持久化成功、恢复执行时无需再次推理的样本标识。
            on_sample_result: 每条新样本评分完成后接收结果和整体进度的可选回调。

        Returns:
            样本级结果列表以及由这些结果聚合出的任务报告。

        Raises:
            Exception: 模型调用、评分或报告构建失败时，标记任务失败后原样传播。
        """
        # 进入执行器即记录运行态；结果列表保持与输入样本完全相同的顺序。
        job.mark_running()
        results: list[EvaluationSampleResult] = []
        completed = sum(1 for sample in samples if sample.id in skip_sample_ids)

        try:
            # 任务级参数覆盖 Benchmark 默认值，让单次运行可以安全调整推理选项。
            runtime_config = {**benchmark.config, **job.runtime_config}
            for sample in samples:
                if sample.id in skip_sample_ids:
                    continue
                # 每条样本先调用模型，再把预测、参考答案和上下文交给统一评测器。
                prediction = self.model_adapter.generate(sample.input, **runtime_config)
                metric = self.evaluator.evaluate(
                    prediction,
                    sample.reference,
                    input_text=sample.input,
                    metadata=sample.metadata,
                )
                # 固化推理与评分快照，使后续报告不需要再次调用外部模型服务。
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
                completed += 1
                if on_sample_result is not None:
                    on_sample_result(results[-1], completed, len(samples))
                # 回调放在结果固化之后，确保外部观察到的进度不会领先于真实产物。
                if on_progress is not None:
                    on_progress(completed, len(samples))

            # 所有样本成功后再生成报告并切换成功态，保证任务状态与产物一致。
            report = build_report(job.id, results)
            job.mark_success()
            return results, report
        except Exception as exc:
            # 捕获边界异常只为记录任务失败状态，随后保留原始异常类型与堆栈。
            job.mark_failed(str(exc))
            raise
