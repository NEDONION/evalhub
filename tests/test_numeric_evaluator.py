"""验证 GSM8K 自由文本按最终数值而非整段字符串评分。"""

from evalhub.evaluators.numeric import NumericExactMatchEvaluator


def test_numeric_evaluator_accepts_reasoning_with_correct_final_number() -> None:
    """推理文本格式不同但最终十进制数相同时必须得到正确分数。"""
    evaluator = NumericExactMatchEvaluator()

    result = evaluator.evaluate(
        "先计算 20 × 25，Final answer: 500",
        "计算过程不同。#### 500",
    )

    assert result.score == 1.0
    assert result.metric == "numeric_exact_match"
