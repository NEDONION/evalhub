from collections.abc import Callable

from evalhub.evaluators.base import Evaluator
from evalhub.evaluators.choice import ChoiceLetterEvaluator
from evalhub.evaluators.exact_match import ExactMatchEvaluator
from evalhub.evaluators.numeric import NumericExactMatchEvaluator

EvaluatorFactory = Callable[[], Evaluator]


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, EvaluatorFactory] = {}

    def register(self, evaluator_type: str, factory: EvaluatorFactory) -> None:
        if not evaluator_type:
            raise ValueError("evaluator_type cannot be empty")
        self._factories[evaluator_type] = factory

    def create(self, evaluator_type: str) -> Evaluator:
        try:
            return self._factories[evaluator_type]()
        except KeyError as exc:
            available = ", ".join(sorted(self._factories)) or "<none>"
            raise KeyError(f"unknown evaluator type: {evaluator_type}; available: {available}") from exc


def default_evaluator_registry() -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register("exact_match", ExactMatchEvaluator)
    registry.register("numeric_exact_match", NumericExactMatchEvaluator)
    registry.register("choice_letter", ChoiceLetterEvaluator)
    return registry
