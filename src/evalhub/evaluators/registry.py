"""注册并按稳定类型名称创建可扩展的评测器插件。"""

from collections.abc import Callable

from evalhub.evaluators.base import Evaluator
from evalhub.evaluators.bbh import BBHAnswerEvaluator
from evalhub.evaluators.choice import ChoiceLetterEvaluator
from evalhub.evaluators.exact_match import ExactMatchEvaluator
from evalhub.evaluators.ifeval import IFEvalStrictEvaluator
from evalhub.evaluators.numeric import NumericExactMatchEvaluator

EvaluatorFactory = Callable[[], Evaluator]


class EvaluatorRegistry:
    """维护评测器类型到无参工厂的映射并负责实例创建。"""

    def __init__(self) -> None:
        """初始化尚未注册任何评测器的工厂表。"""
        # 保存工厂而非共享实例，避免不同任务之间泄漏评测器的可变状态。
        self._factories: dict[str, EvaluatorFactory] = {}

    def register(self, evaluator_type: str, factory: EvaluatorFactory) -> None:
        """注册或替换指定类型名称对应的评测器工厂。

        Args:
            evaluator_type: Benchmark 配置使用的非空稳定类型名称。
            factory: 每次调用都能创建一个评测器实例的无参工厂。

        Raises:
            ValueError: 评测器类型名称为空字符串。
        """
        # 空名称无法形成可引用的配置键，因此在写入注册表前立即拒绝。
        if not evaluator_type:
            raise ValueError("evaluator_type cannot be empty")
        self._factories[evaluator_type] = factory

    def create(self, evaluator_type: str) -> Evaluator:
        """按类型名称创建新的评测器实例。

        Raises:
            KeyError: 类型尚未注册，并在错误中列出当前可用类型。
        """
        try:
            return self._factories[evaluator_type]()
        except KeyError as exc:
            # 稳定排序可用类型，保证错误信息便于阅读且不会随注册顺序波动。
            available = ", ".join(sorted(self._factories)) or "<none>"
            message = f"unknown evaluator type: {evaluator_type}; available: {available}"
            raise KeyError(message) from exc


def default_evaluator_registry() -> EvaluatorRegistry:
    """创建包含仓库内置指标实现的新注册表。"""
    # 每次构造独立注册表，允许调用方扩展而不污染其他任务的默认配置。
    registry = EvaluatorRegistry()
    registry.register("bbh_answer", BBHAnswerEvaluator)
    registry.register("exact_match", ExactMatchEvaluator)
    registry.register("numeric_exact_match", NumericExactMatchEvaluator)
    registry.register("choice_letter", ChoiceLetterEvaluator)
    registry.register("ifeval_strict", IFEvalStrictEvaluator)
    return registry
