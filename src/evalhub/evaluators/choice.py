"""实现从自由文本提取 A 至 D 选项字母的选择题指标。"""

import re

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator


class ChoiceLetterEvaluator(Evaluator):
    """提取预测与参考答案中的选项字母并进行精确比较。"""

    # 稳定指标名用于 Benchmark 配置和任务报告中的评测器识别。
    metric_name = "choice_letter"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """提取两侧选项并计算二值选择题得分。

        Args:
            prediction: 可能包含解释文字的模型选择题回答。
            reference: 数据集提供的标准选项文本。
            input_text: 当前指标不使用但为统一接口保留的原始输入。
            metadata: 当前指标不使用但为扩展保留的样本元数据。

        Returns:
            选项一致时得分为 1，否则附带期望与实际选项。
        """
        # 预测与参考答案使用同一提取规则，避免因格式差异导致非对称判断。
        predicted_letter = _extract_choice(prediction)
        reference_letter = _extract_choice(reference)
        matched = predicted_letter is not None and predicted_letter == reference_letter
        # 统一返回领域指标对象，便于 Runner 与其他评测器共享聚合逻辑。
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None if matched else f"expected {reference_letter}, got {predicted_letter}",
        )


def _extract_choice(value: str) -> str | None:
    """从文本中提取最可信的 A、B、C 或 D 选项字母。

    Args:
        value: 可能包含大小写差异、解释文字或显式答案前缀的文本。

    Returns:
        提取到的标准大写选项；没有独立选项字母时返回 ``None``。
    """
    # 先统一大小写和边界空白，使中英文冒号形式可以共享后续正则。
    normalized = value.strip().upper()
    answer_match = re.search(r"ANSWER\s*[:：]\s*([ABCD])\b", normalized)
    if answer_match:
        # 显式 ``ANSWER:`` 前缀具有最高置信度，应优先于解释中出现的其他字母。
        return answer_match.group(1)
    # 没有显式前缀时选择最后一个独立选项字母，适配“分析后给出结论”的回答。
    matches = re.findall(r"\b([ABCD])\b", normalized)
    return matches[-1] if matches else None
