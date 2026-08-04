"""验证 Hexagon 所选 IFEval 官方严格规则的本地评分行为。"""

import pytest

from evalhub.evaluators.ifeval import SUPPORTED_RULE_IDS, IFEvalStrictEvaluator
from evalhub.evaluators.registry import default_evaluator_registry


@pytest.mark.parametrize(
    ("instruction_id", "kwargs", "prediction", "expected"),
    [
        ("punctuation:no_comma", {}, "No commas here", 1.0),
        ("punctuation:no_comma", {}, "No, commas here", 0.0),
        ("detectable_format:json_format", {}, '{"ok": true}', 1.0),
        ("detectable_format:json_format", {}, "not json", 0.0),
        ("detectable_content:postscript", {"postscript_marker": "P.S."}, "Body\nP.S. note", 1.0),
        (
            "detectable_format:multiple_sections",
            {"section_spliter": "Section", "num_sections": 2},
            "Section 1\na\nSection 2\nb",
            1.0,
        ),
        ("startend:quotation", {}, '"quoted response"', 1.0),
    ],
)
def test_ifeval_strict_rules(
    instruction_id: str,
    kwargs: dict[str, object],
    prediction: str,
    expected: float,
) -> None:
    """每项基础官方规则应按其给定参数返回二值得分。

    Args:
        instruction_id: 当前样本指定的官方规则标识。
        kwargs: 官方 JSONL 为该规则冻结的参数对象。
        prediction: 供规则检查的模型回答夹具。
        expected: 独立推导出的预期二值得分。
    """
    result = IFEvalStrictEvaluator().evaluate(
        prediction,
        "",
        metadata={"instruction_id_list": [instruction_id], "kwargs": [kwargs]},
    )

    assert result.score == expected


@pytest.mark.parametrize(
    ("instruction_id", "kwargs", "passing", "failing"),
    [
        (
            "detectable_content:number_placeholders",
            {"num_placeholders": 2},
            "[hotel] and [restaurant]",
            "[hotel] only",
        ),
        (
            "detectable_format:number_bullet_lists",
            {"num_bullets": 1},
            "* one bullet",
            "* first\n- second",
        ),
        (
            "detectable_format:number_highlighted_sections",
            {"num_highlights": 2},
            "*one* and *two*",
            "*one* only",
        ),
        ("detectable_format:title", {}, "<<A real title>>", "<<   >>"),
        (
            "startend:end_checker",
            {"end_phrase": "Done."},
            'Advice ends with "DONE."',
            "Done. More advice",
        ),
    ],
)
def test_ifeval_remaining_selected_rules(
    instruction_id: str,
    kwargs: dict[str, object],
    passing: str,
    failing: str,
) -> None:
    """其余选中规则应区分满足和不满足官方格式的真实文本。

    Args:
        instruction_id: 需要验证的 IFEval 规则标识。
        kwargs: 该规则的冻结官方参数。
        passing: 按官方语义应通过的回答文本。
        failing: 仅破坏当前规则条件的回答文本。
    """
    metadata = {"instruction_id_list": [instruction_id], "kwargs": [kwargs]}
    evaluator = IFEvalStrictEvaluator()

    assert evaluator.evaluate(passing, "", metadata=metadata).score == 1.0
    assert evaluator.evaluate(failing, "", metadata=metadata).score == 0.0


def test_ifeval_requires_every_instruction_to_pass() -> None:
    """同一题的任一规则失败时，提示级严格得分必须为零。"""
    result = IFEvalStrictEvaluator().evaluate(
        '"contains, comma"',
        "",
        metadata={
            "instruction_id_list": ["startend:quotation", "punctuation:no_comma"],
            "kwargs": [{}, {}],
        },
    )

    assert result.score == 0.0


def test_ifeval_rejects_unsupported_rule_metadata() -> None:
    """未知规则不能被悄悄当作通过或失败，必须显式拒绝。"""
    with pytest.raises(ValueError, match="unsupported IFEval instruction"):
        IFEvalStrictEvaluator().evaluate(
            "response",
            "",
            metadata={"instruction_id_list": ["unsupported:rule"], "kwargs": [{}]},
        )


def test_ifeval_registry_creates_strict_evaluator() -> None:
    """默认注册表应为 Hexagon IFEval 配置提供严格规则评分器。"""
    evaluator = default_evaluator_registry().create("ifeval_strict")

    assert isinstance(evaluator, IFEvalStrictEvaluator)
    assert len(SUPPORTED_RULE_IDS) == 10
