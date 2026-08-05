"""实现 Hexagon 所选 BBH 子任务的高置信度答案解析。"""

import re

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator

_ANSWER_DOMAINS = {
    "boolean_expressions": frozenset({"true", "false"}),
    "causal_judgement": frozenset({"yes", "no"}),
    "date_understanding": frozenset("abcdef"),
    "disambiguation_qa": frozenset("abc"),
    "formal_fallacies": frozenset({"valid", "invalid"}),
}
_EXPLICIT_ANSWER = re.compile(
    r"(?:final\s+answer|answer)\s*(?:(?:is)\s*:?\s*|:\s*)([^\n]+)",
    flags=re.IGNORECASE,
)
_BOXED_ANSWER = re.compile(r"\\boxed\{([^{}]+)\}")


class BBHAnswerEvaluator(Evaluator):
    """按 BBH 子任务答案域比较显式结论，避免从解释中误抓 token。"""

    metric_name = "bbh_answer"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """提取当前 BBH 子任务的高置信度最终答案并进行二值评分。

        Args:
            prediction: 可能包含解释、显式答案或 LaTeX boxed 结论的模型输出。
            reference: 官方 BBH 目标值。
            input_text: 当前评分不使用但为统一接口保留的原始输入。
            metadata: 必须包含固定来源解析出的官方 ``task`` 名称。

        Returns:
            任务受支持且答案唯一匹配时得 1，否则返回零分和解析原因。
        """
        task = metadata.get("task") if metadata else None
        if not isinstance(task, str) or task not in _ANSWER_DOMAINS:
            return MetricResult(
                metric=self.metric_name,
                score=0.0,
                reason=f"unsupported BBH task: {task}",
            )
        allowed = _ANSWER_DOMAINS[task]
        expected = _canonical_answer(reference, allowed)
        actual = _extract_prediction(prediction, allowed)
        matched = expected is not None and actual == expected
        # 解析失败和真实答错共享零分，但原因保留规范化值以支持失败样例排查。
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if matched else 0.0,
            reason=None if matched else f"expected {expected}, got {actual}",
        )


def _extract_prediction(value: str, allowed: frozenset[str]) -> str | None:
    """按显式答案、boxed 答案、独立末行顺序提取唯一结论。

    Args:
        value: 模型生成的原始输出。
        allowed: 当前任务允许的规范化答案集合。

    Returns:
        唯一合法答案；不存在、格式不可信或结论冲突时返回 ``None``。
    """
    explicit = _EXPLICIT_ANSWER.findall(value)
    if explicit:
        return _single_candidate(explicit, allowed)
    boxed = _BOXED_ANSWER.findall(value)
    if boxed:
        return _single_candidate(boxed, allowed)
    # 仅检查最后一个非空行，防止解释过程中的 True、No 或选项字母被误判为结论。
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return _canonical_answer(lines[-1], allowed) if lines else None


def _single_candidate(values: list[str], allowed: frozenset[str]) -> str | None:
    """要求所有显式候选合法且彼此一致。

    Args:
        values: 同一优先级提取出的原始候选列表。
        allowed: 当前任务允许的规范化答案集合。

    Returns:
        唯一规范化答案；任一候选无效或候选冲突时返回 ``None``。
    """
    candidates = [_canonical_answer(value, allowed) for value in values]
    if any(candidate is None for candidate in candidates):
        return None
    unique = set(candidates)
    return unique.pop() if len(unique) == 1 else None


def _canonical_answer(value: str, allowed: frozenset[str]) -> str | None:
    """把一个完整候选规范化到当前任务的答案域。

    Args:
        value: 不应包含解释文字的单个原始答案候选。
        allowed: 当前任务允许的规范化答案集合。

    Returns:
        小写答案 token；完整候选不属于答案域时返回 ``None``。
    """
    normalized = value.strip().lower().rstrip(".。")
    markdown_wrapper = re.fullmatch(r"(?:\*\*(.+)\*\*|`(.+)`)", normalized)
    if markdown_wrapper:
        # 只剥离包住整个答案的单层粗体或行内代码，解释正文仍不会进入答案提取。
        normalized = next(group for group in markdown_wrapper.groups() if group).strip()
    choice = re.fullmatch(r"\(?([a-f])\)?", normalized)
    candidate = choice.group(1) if choice else normalized
    return candidate if candidate in allowed else None
