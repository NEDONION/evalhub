import re

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator


class ChoiceLetterEvaluator(Evaluator):
    metric_name = "choice_letter"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        predicted_letter = _extract_choice(prediction)
        reference_letter = _extract_choice(reference)
        matched = predicted_letter is not None and predicted_letter == reference_letter
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None if matched else f"expected {reference_letter}, got {predicted_letter}",
        )


def _extract_choice(value: str) -> str | None:
    normalized = value.strip().upper()
    answer_match = re.search(r"ANSWER\s*[:：]\s*([ABCD])\b", normalized)
    if answer_match:
        return answer_match.group(1)
    matches = re.findall(r"\b([ABCD])\b", normalized)
    return matches[-1] if matches else None
