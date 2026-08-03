"""实现从自由文本提取末尾数值并进行十进制精确比较的指标。"""

import re
from decimal import Decimal, InvalidOperation

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator


class NumericExactMatchEvaluator(Evaluator):
    """比较预测与参考答案中最后一个可解析十进制数值。"""

    # 独立指标名用于区分文本精确匹配，避免报告消费者混淆评分语义。
    metric_name = "numeric_exact_match"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """提取两侧数值并计算二值精确匹配分数。

        Args:
            prediction: 可能包含推理过程的模型回答。
            reference: 可能包含题解与最终答案的参考文本。
            input_text: 当前指标不使用但为统一接口保留的原始输入。
            metadata: 当前指标不使用但为扩展保留的样本元数据。

        Returns:
            数值相等时得分为 1，否则返回包含两侧解析结果的失败原因。
        """
        # 两侧复用同一提取器，确保千位分隔符和 GSM8K 答案标记处理一致。
        prediction_number = _extract_number(prediction)
        reference_number = _extract_number(reference)
        matched = prediction_number is not None and prediction_number == reference_number
        # 解析失败与数值不等都视为未匹配，并在原因中保留实际解析结果便于诊断。
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None
            if matched
            else f"expected numeric answer {reference_number}, got {prediction_number}",
        )


def _extract_number(value: str) -> Decimal | None:
    """从文本中提取最后一个合法十进制数值。

    Args:
        value: 可能包含自然语言、千位分隔符或 GSM8K 最终答案标记的文本。

    Returns:
        成功时返回精确 ``Decimal``，没有合法数值时返回 ``None``。
    """
    # GSM8K 使用 ``####`` 分隔最终答案，存在标记时只分析其后的结论部分。
    if "####" in value:
        value = value.split("####")[-1]
    # 正则兼容正负号、逗号千位分隔和小数，并选择文本中最后出现的候选值。
    matches = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    if not matches:
        return None
    try:
        # 移除展示用千位分隔符后使用 Decimal，避免二进制浮点比较误差。
        return Decimal(matches[-1].replace(",", ""))
    except InvalidOperation:
        # 极端格式即使被正则捕获也可能无法构造 Decimal，此时按无有效答案处理。
        return None
