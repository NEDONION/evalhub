from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator


class ExactMatchEvaluator(Evaluator):
    metric_name = "exact_match"

    def __init__(self, *, ignore_case: bool = True, strip: bool = True) -> None:
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
        normalized_prediction = self._normalize(prediction)
        normalized_reference = self._normalize(reference)
        matched = normalized_prediction == normalized_reference
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None if matched else "prediction does not exactly match reference",
        )

    def _normalize(self, value: str) -> str:
        if self.strip:
            value = value.strip()
        if self.ignore_case:
            value = value.casefold()
        return value
