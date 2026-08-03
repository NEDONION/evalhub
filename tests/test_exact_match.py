from evalhub.evaluators import ExactMatchEvaluator


def test_exact_match_normalizes_case_and_whitespace() -> None:
    evaluator = ExactMatchEvaluator()

    result = evaluator.evaluate("  Answer  ", "answer")

    assert result.metric == "exact_match"
    assert result.score == 1.0
    assert result.reason is None


def test_exact_match_returns_zero_for_mismatch() -> None:
    evaluator = ExactMatchEvaluator()

    result = evaluator.evaluate("4", "5")

    assert result.score == 0.0
    assert result.reason is not None
