from decimal import Decimal, InvalidOperation
import re

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator


class NumericExactMatchEvaluator(Evaluator):
    metric_name = "numeric_exact_match"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        prediction_number = _extract_number(prediction)
        reference_number = _extract_number(reference)
        matched = prediction_number is not None and prediction_number == reference_number
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None
            if matched
            else f"expected numeric answer {reference_number}, got {prediction_number}",
        )


def _extract_number(value: str) -> Decimal | None:
    if "####" in value:
        value = value.split("####")[-1]
    matches = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    if not matches:
        return None
    try:
        return Decimal(matches[-1].replace(",", ""))
    except InvalidOperation:
        return None
