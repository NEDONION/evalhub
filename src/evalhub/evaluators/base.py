"""声明样本级评测策略必须实现的统一评分接口。"""

from abc import ABC, abstractmethod

from evalhub.domain.entities import MetricResult


class Evaluator(ABC):
    """把模型预测与参考答案转换为标准指标结果的抽象基类。"""

    # 每个实现使用稳定指标名，供 Benchmark 配置、报告聚合和结果查询引用。
    metric_name: str

    @abstractmethod
    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """评估单条预测并返回标准化指标结果。

        Args:
            prediction: 模型对当前样本生成的文本。
            reference: 数据集提供的参考答案。
            input_text: 可选的原始样本输入，供上下文相关指标使用。
            metadata: 可选的数据集元数据，供高级评测器扩展。

        Returns:
            包含指标名、分数和可选原因的样本级结果。
        """
        # 抽象实现显式失败，保证注册到执行器的评测器真正提供评分逻辑。
        raise NotImplementedError
