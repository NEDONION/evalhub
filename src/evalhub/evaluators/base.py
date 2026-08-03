from abc import ABC, abstractmethod

from evalhub.domain.entities import MetricResult


class Evaluator(ABC):
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
        raise NotImplementedError
