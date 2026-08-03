"""实现支持大小写与首尾空白归一化的精确文本匹配指标。"""

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator


class ExactMatchEvaluator(Evaluator):
    """在可配置文本归一化后判断预测与参考答案是否完全一致。"""

    # 指标名进入样本结果和任务报告，必须保持跨版本稳定。
    metric_name = "exact_match"

    def __init__(self, *, ignore_case: bool = True, strip: bool = True) -> None:
        """配置精确匹配前是否忽略大小写和首尾空白。

        Args:
            ignore_case: 为真时使用 Unicode 友好的大小写折叠。
            strip: 为真时移除文本首尾空白。
        """
        # 归一化选项保存在实例上，使同一任务的所有样本使用一致规则。
        self.ignore_case = ignore_case
        self.strip = strip

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """归一化预测和参考答案后计算二值精确匹配分数。

        Args:
            prediction: 模型生成的待比较文本。
            reference: 数据集提供的标准答案文本。
            input_text: 当前指标不使用但为统一接口保留的原始输入。
            metadata: 当前指标不使用但为扩展保留的样本元数据。

        Returns:
            匹配时得分为 1，否则得分为 0 并附带不匹配原因。
        """
        # 两侧必须应用完全相同的归一化顺序，避免比较规则出现非对称偏差。
        normalized_prediction = self._normalize(prediction)
        normalized_reference = self._normalize(reference)
        matched = normalized_prediction == normalized_reference
        # 使用标准领域结果承载二值分数，让引擎可以与其他指标统一聚合。
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None if matched else "prediction does not exactly match reference",
        )

    def _normalize(self, value: str) -> str:
        """按实例配置依次清理空白并执行 Unicode 大小写折叠。"""
        # 先移除边界空白，再折叠大小写，保持归一化步骤直观且可预测。
        if self.strip:
            value = value.strip()
        if self.ignore_case:
            value = value.casefold()
        return value
