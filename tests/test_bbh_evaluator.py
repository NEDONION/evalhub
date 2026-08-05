"""验证 Hexagon 选定 BBH 子任务的任务感知答案解析。"""

import pytest

from evalhub.evaluators.bbh import BBHAnswerEvaluator


@pytest.mark.parametrize(
    ("task", "prediction", "reference"),
    [
        ("boolean_expressions", "分析完成。\nAnswer: True", "True"),
        ("boolean_expressions", "The final answer is: **True**", "True"),
        ("causal_judgement", "The final answer is no.", "No"),
        ("date_understanding", r"结论是 \boxed{(F)}", "(F)"),
        ("disambiguation_qa", "解释内容\n(C)", "(C)"),
        ("formal_fallacies", "Final answer: invalid", "invalid"),
    ],
)
def test_bbh_evaluator_accepts_high_confidence_task_specific_answers(
    task: str, prediction: str, reference: str
) -> None:
    """显式答案、单个 boxed 答案或独立末行应按当前任务答案域规范化。"""
    evaluator = BBHAnswerEvaluator()

    result = evaluator.evaluate(prediction, reference, metadata={"task": task})

    assert result.score == 1.0
    assert result.metric == "bbh_answer"


def test_bbh_evaluator_does_not_extract_incidental_token_from_explanation() -> None:
    """解释中偶然出现参考 token 但没有最终结论时不得判为正确。"""
    evaluator = BBHAnswerEvaluator()

    result = evaluator.evaluate(
        "If the premise is True, another branch follows.\nI cannot decide.",
        "True",
        metadata={"task": "boolean_expressions"},
    )

    assert result.score == 0.0


def test_bbh_evaluator_rejects_conflicting_explicit_answers() -> None:
    """同一预测给出两个不同显式结论时应视为歧义并返回零分。"""
    evaluator = BBHAnswerEvaluator()

    result = evaluator.evaluate(
        "Answer: valid\nFinal answer: invalid",
        "invalid",
        metadata={"task": "formal_fallacies"},
    )

    assert result.score == 0.0


def test_bbh_evaluator_rejects_unknown_task_protocol() -> None:
    """未登记的 BBH 子任务不能借用其他答案域进行猜测评分。"""
    evaluator = BBHAnswerEvaluator()

    result = evaluator.evaluate("Answer: A", "(A)", metadata={"task": "unknown"})

    assert result.score == 0.0
    assert "unsupported BBH task" in str(result.reason)
