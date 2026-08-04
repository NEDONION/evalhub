# Copyright 2026 EvalHub Authors.
#
# Portions of the rule semantics in this file are adapted from Google Research
# IFEval at revision 8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0.
# Copyright 2026 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""实现 Hexagon 选定十条 IFEval 官方提示级严格评分规则。"""

import json
import re
from collections.abc import Callable, Mapping

from evalhub.domain.entities import MetricResult
from evalhub.evaluators.base import Evaluator

RuleChecker = Callable[[str, Mapping[str, object]], bool]

SUPPORTED_RULE_IDS = frozenset(
    {
        "punctuation:no_comma",
        "detectable_content:postscript",
        "startend:quotation",
        "detectable_format:json_format",
        "detectable_content:number_placeholders",
        "detectable_format:number_bullet_lists",
        "detectable_format:number_highlighted_sections",
        "detectable_format:multiple_sections",
        "detectable_format:title",
        "startend:end_checker",
    }
)


class IFEvalStrictEvaluator(Evaluator):
    """按固定 IFEval 官方规则逐项校验，并以全规则通过作为题目得分。"""

    metric_name = "ifeval_prompt_strict"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        """根据样本元数据中的 IFEval 规则计算提示级严格得分。

        Args:
            prediction: 模型对英文 IFEval 提示生成的完整回答。
            reference: IFEval 没有文本参考答案，保留该参数以兼容统一接口。
            input_text: 当前规则不读取的原始英文提示，保留以兼容统一接口。
            metadata: 包含等长 ``instruction_id_list`` 和 ``kwargs`` 列表的样本元数据。

        Returns:
            全部规则通过且回答非空时为 1，否则为 0 的标准指标结果。

        Raises:
            ValueError: 规则元数据缺失、类型错误、长度不一致或包含未支持规则时抛出。
        """
        instruction_ids, arguments = validate_ifeval_rules(metadata)
        # 每项检查保留原始清单顺序，确保规则失败能够定位到官方稳定标识。
        checks = [
            {
                "instruction_id": instruction_id,
                "passed": _RULES[instruction_id](prediction, item_arguments),
            }
            for instruction_id, item_arguments in zip(instruction_ids, arguments, strict=True)
        ]
        passed = bool(prediction.strip()) and all(item["passed"] for item in checks)
        # 空回答即使偶然满足无逗号等形式规则也不能计为完成该提示。
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if passed else 0.0,
            reason=None if passed else "one or more IFEval rules failed",
            metadata={"checks": checks},
        )


def validate_ifeval_rules(
    metadata: Mapping[str, object] | None,
) -> tuple[list[str], list[dict[str, object]]]:
    """验证选中 IFEval 规则及参数，并返回可供加载器和评分器共享的收窄结果。

    Args:
        metadata: 加载器从 IFEval 官方 JSONL 传入的样本元数据。

    Returns:
        已确认规则均受支持、顺序对应的规则标识和参数对象列表。

    Raises:
        ValueError: 元数据结构、规则标识或规则参数不符合本地十条规则契约时抛出。
    """
    if metadata is None:
        raise ValueError("IFEval metadata is required")
    instruction_ids = metadata.get("instruction_id_list")
    arguments = metadata.get("kwargs")
    if not isinstance(instruction_ids, list) or not isinstance(arguments, list):
        raise ValueError("IFEval metadata must contain rule and kwargs lists")
    if not instruction_ids or len(instruction_ids) != len(arguments):
        raise ValueError("IFEval rule and kwargs lists must be non-empty and equally sized")
    # 在评分前完成类型和支持范围收窄，避免不完整来源记录被静默计分。
    if any(not isinstance(item, str) for item in instruction_ids):
        raise ValueError("IFEval instruction IDs must be strings")
    unsupported = [item for item in instruction_ids if item not in SUPPORTED_RULE_IDS]
    if unsupported:
        raise ValueError(f"unsupported IFEval instruction: {unsupported[0]}")
    if any(not isinstance(item, dict) for item in arguments):
        raise ValueError("IFEval kwargs entries must be objects")
    # 固定清单只允许这十条规则的原始官方参数形状，加载和评分必须共用同一边界。
    for instruction_id, item_arguments in zip(instruction_ids, arguments, strict=True):
        _validate_rule_arguments(instruction_id, item_arguments)
    return instruction_ids, arguments


def _validated_int(arguments: Mapping[str, object], name: str) -> int:
    """读取固定规则需要的非负整数参数，拒绝会触发官方随机回退的越界值。

    Args:
        arguments: 当前 IFEval 规则冻结的参数对象。
        name: 需要读取的整数参数名称。

    Returns:
        官方 JSONL 中提供的整数值。

    Raises:
        ValueError: 参数缺失、是布尔值、不是整数或为负数时抛出。
    """
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid IFEval rule arguments")
    return value


def _validated_text(arguments: Mapping[str, object], name: str) -> str:
    """读取固定规则需要的非空字符串参数，避免空值成为无条件匹配的正则。

    Args:
        arguments: 当前 IFEval 规则冻结的参数对象。
        name: 需要读取的字符串参数名称。

    Returns:
        按官方构建器语义移除首尾空白后的参数文本。

    Raises:
        ValueError: 参数缺失、不是字符串或仅含空白时抛出。
    """
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid IFEval rule arguments")
    return value.strip()


def _validate_rule_arguments(instruction_id: str, arguments: Mapping[str, object]) -> None:
    """按固定规则参数表验证名称、非空文本和非负整数，阻止官方随机回退。

    Args:
        instruction_id: 已确认受支持的 IFEval 官方规则标识。
        arguments: 当前规则的官方参数对象。

    Raises:
        ValueError: 参数名、类型、空白文本或负整数不符合固定规则契约时抛出。
    """
    names, text_names, integer_names = _RULE_ARGUMENTS[instruction_id]
    if set(arguments) != names:
        raise ValueError("invalid IFEval rule arguments")
    # 同一表同时覆盖无参数规则与带参数规则，避免加载器、评分器维护两套 schema。
    for name in text_names:
        _validated_text(arguments, name)
    for name in integer_names:
        _validated_int(arguments, name)


def _no_comma(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 CommaChecker 判断整个回答是否不含英文逗号。

    Args:
        value: 需要检查的模型回答。
        arguments: 此无参数规则的冻结参数对象。

    Returns:
        回答中不存在英文逗号时返回 ``True``。
    """
    return re.search(r"\,", value) is None


def _postscript(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 PostscriptChecker 匹配 P.S.、P.P.S 或自定义附注起始标记。

    Args:
        value: 需要检查的模型回答。
        arguments: 必须含 ``postscript_marker`` 的官方规则参数。

    Returns:
        存在匹配附注标记时返回 ``True``。
    """
    marker = _validated_text(arguments, "postscript_marker")
    normalized = value.lower()
    # 官方实现对两个默认标记容忍标点后的可选空白，其他标记按原正则拼接。
    if marker == "P.P.S":
        pattern = r"\s*p\.\s?p\.\s?s.*$"
    elif marker == "P.S.":
        pattern = r"\s*p\.\s?s\..*$"
    else:
        pattern = r"\s*" + marker.lower() + r".*$"
    return bool(re.findall(pattern, normalized, flags=re.MULTILINE))


def _quotation(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 QuotationChecker 判断回答整体是否由双引号包围。

    Args:
        value: 需要检查的模型回答。
        arguments: 此无参数规则的冻结参数对象。

    Returns:
        去除边界空白后首尾均为双引号且长度大于一时返回 ``True``。
    """
    normalized = value.strip()
    return len(normalized) > 1 and normalized[0] == '"' and normalized[-1] == '"'


def _json_format(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 JsonFormat 允许指定 Markdown 围栏后验证 JSON 解析。

    Args:
        value: 需要检查的模型回答。
        arguments: 此无参数规则的冻结参数对象。

    Returns:
        移除官方允许的单层围栏后可由 ``json.loads`` 解析时返回 ``True``。
    """
    normalized = value.strip()
    # 移除顺序与钉住 revision 保持一致，因此只接受官方认可的四种开头。
    normalized = normalized.removeprefix("```json").removeprefix("```Json")
    normalized = normalized.removeprefix("```JSON").removeprefix("```")
    normalized = normalized.removesuffix("```").strip()
    try:
        json.loads(normalized)
    except ValueError:
        return False
    return True


def _number_placeholders(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 PlaceholderChecker 统计方括号占位符并检查最小数量。

    Args:
        value: 需要检查的模型回答。
        arguments: 必须含 ``num_placeholders`` 的官方规则参数。

    Returns:
        非贪婪方括号匹配数量不少于要求时返回 ``True``。
    """
    required = _validated_int(arguments, "num_placeholders")
    return len(re.findall(r"\[.*?\]", value)) >= required


def _number_bullet_lists(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 BulletListChecker 统计星号和连字符 Markdown 项目符号。

    Args:
        value: 需要检查的模型回答。
        arguments: 必须含 ``num_bullets`` 的官方规则参数。

    Returns:
        两种官方正则匹配项目总数恰好等于要求时返回 ``True``。
    """
    required = _validated_int(arguments, "num_bullets")
    stars = re.findall(r"^\s*\*[^\*].*$", value, flags=re.MULTILINE)
    hyphens = re.findall(r"^\s*-.*$", value, flags=re.MULTILINE)
    return len(stars) + len(hyphens) == required


def _number_highlighted_sections(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 HighlightSectionChecker 统计非空单星号和双星号高亮。

    Args:
        value: 需要检查的模型回答。
        arguments: 必须含 ``num_highlights`` 的官方规则参数。

    Returns:
        有效高亮数量不少于要求时返回 ``True``。
    """
    required = _validated_int(arguments, "num_highlights")
    highlights = re.findall(r"\*[^\n\*]*\*", value)
    double_highlights = re.findall(r"\*\*[^\n\*]*\*\*", value)
    # 两组匹配独立累加，保留官方实现在双星号文本上的原始计数语义。
    count = sum(bool(item.strip("*").strip()) for item in highlights)
    count += sum(
        bool(item.removeprefix("**").removesuffix("**").strip())
        for item in double_highlights
    )
    return count >= required


def _multiple_sections(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 SectionChecker 用指定标记加数字分隔并统计章节数量。

    Args:
        value: 需要检查的模型回答。
        arguments: 必须含 ``section_spliter`` 和 ``num_sections`` 的规则参数。

    Returns:
        官方正则分割出的章节数不少于要求时返回 ``True``。
    """
    splitter = _validated_text(arguments, "section_spliter")
    required = _validated_int(arguments, "num_sections")
    pattern = r"\s?" + splitter + r"\s?\d+\s?"
    return len(re.split(pattern, value)) - 1 >= required


def _title(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 TitleChecker 查找含非空内容的双尖括号单行标题。

    Args:
        value: 需要检查的模型回答。
        arguments: 此无参数规则的冻结参数对象。

    Returns:
        至少有一个非空 ``<<title>>`` 形式标题时返回 ``True``。
    """
    titles = re.findall(re.compile(r"<<[^\n]+>>"), value)
    return any(item.lstrip("<").rstrip(">").strip() for item in titles)


def _end_checker(value: str, arguments: Mapping[str, object]) -> bool:
    """按官方 EndChecker 忽略边界空白和外层引号后校验结尾短语。

    Args:
        value: 需要检查的模型回答。
        arguments: 必须含 ``end_phrase`` 的官方规则参数。

    Returns:
        按不区分大小写的官方结尾匹配规则通过时返回 ``True``。
    """
    ending = _validated_text(arguments, "end_phrase").lower()
    return value.strip().strip('"').lower().endswith(ending)


_RULES: dict[str, RuleChecker] = {
    "punctuation:no_comma": _no_comma,
    "detectable_content:postscript": _postscript,
    "startend:quotation": _quotation,
    "detectable_format:json_format": _json_format,
    "detectable_content:number_placeholders": _number_placeholders,
    "detectable_format:number_bullet_lists": _number_bullet_lists,
    "detectable_format:number_highlighted_sections": _number_highlighted_sections,
    "detectable_format:multiple_sections": _multiple_sections,
    "detectable_format:title": _title,
    "startend:end_checker": _end_checker,
}

_RULE_ARGUMENTS: dict[str, tuple[set[str], set[str], set[str]]] = {
    "punctuation:no_comma": (set(), set(), set()),
    "detectable_content:postscript": ({"postscript_marker"}, {"postscript_marker"}, set()),
    "startend:quotation": (set(), set(), set()),
    "detectable_format:json_format": (set(), set(), set()),
    "detectable_content:number_placeholders": (
        {"num_placeholders"},
        set(),
        {"num_placeholders"},
    ),
    "detectable_format:number_bullet_lists": ({"num_bullets"}, set(), {"num_bullets"}),
    "detectable_format:number_highlighted_sections": (
        {"num_highlights"},
        set(),
        {"num_highlights"},
    ),
    "detectable_format:multiple_sections": (
        {"section_spliter", "num_sections"},
        {"section_spliter"},
        {"num_sections"},
    ),
    "detectable_format:title": (set(), set(), set()),
    "startend:end_checker": ({"end_phrase"}, {"end_phrase"}, set()),
}
