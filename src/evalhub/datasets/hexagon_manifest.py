"""读取并校验 Hexagon 固定样本清单的双语来源溯源信息。"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from evalhub.benchmarks.models import Capability

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_HEXAGON_BENCHMARK_IDS = (
    "hexagon-mmlu",
    "hexagon-ifeval",
    "hexagon-gsm8k",
    "hexagon-bbh",
    "hexagon-humaneval",
    "hexagon-truthfulqa",
    "hexagon-bbq",
)
_EXPECTED_COUNTS = dict(zip(_HEXAGON_BENCHMARK_IDS, (5, 5, 5, 5, 5, 3, 2), strict=True))
_EXPECTED_CAPABILITIES = {
    "hexagon-mmlu": Capability.KNOWLEDGE,
    "hexagon-ifeval": Capability.INSTRUCTION_FOLLOWING,
    "hexagon-gsm8k": Capability.MATHEMATICS,
    "hexagon-bbh": Capability.REASONING,
    "hexagon-humaneval": Capability.CODING,
    "hexagon-truthfulqa": Capability.SAFETY_TRUST,
    "hexagon-bbq": Capability.SAFETY_TRUST,
}
_EXPECTED_STRATA = {
    "hexagon-mmlu": {
        "abstract_algebra",
        "anatomy",
        "business_ethics",
        "college_computer_science",
        "econometrics",
    },
    "hexagon-ifeval": {
        "punctuation:no_comma",
        "detectable_content:postscript",
        "startend:quotation",
        "detectable_format:json_format",
        "detectable_content:number_placeholders",
    },
    "hexagon-gsm8k": {"test"},
    "hexagon-bbh": {
        "boolean_expressions",
        "causal_judgement",
        "date_understanding",
        "disambiguation_qa",
        "formal_fallacies",
    },
    "hexagon-humaneval": {
        "HumanEval/126",
        "HumanEval/84",
        "HumanEval/108",
        "HumanEval/30",
        "HumanEval/24",
    },
    "hexagon-truthfulqa": {
        "Misconceptions",
        "Health",
        "Conspiracies",
    },
    "hexagon-bbq": {
        "Age/ambig",
        "Disability_status/disambig",
    },
}
IFEVAL_SELECTED_SOURCE_CONTRACTS: dict[
    str, tuple[tuple[str, ...], tuple[tuple[tuple[str, object], ...], ...]]
] = {
    "32": (("punctuation:no_comma",), ((),)),
    "1759": (("detectable_content:postscript",), ((("postscript_marker", "P.S."),),)),
    "2829": (("startend:quotation",), ((),)),
    "321": (("detectable_format:json_format",), ((),)),
    "3221": (("detectable_content:number_placeholders",), ((("num_placeholders", 8),),)),
    "2832": (("detectable_format:number_bullet_lists",), ((("num_bullets", 1),),)),
    "2253": (("detectable_format:number_highlighted_sections",), ((("num_highlights", 3),),)),
    "2925": (
        ("detectable_format:multiple_sections",),
        ((("section_spliter", "SECTION"), ("num_sections", 3)),),
    ),
    "1551": (("detectable_format:title",), ((),)),
    "1659": (
        ("startend:end_checker",),
        ((("end_phrase", "You cannot fail with the steps listed above."),),),
    ),
}
_IFEVAL_SOURCE_KEYS = {
    instruction_ids[0]: source_key
    for source_key, (instruction_ids, _) in IFEVAL_SELECTED_SOURCE_CONTRACTS.items()
}
_TRUTHFULQA_OPTION_ORDERS = (("A", "B"), ("B", "A"), ("A", "B"))


@dataclass(frozen=True)
class HexagonSampleSpec:
    """描述一个冻结的 Hexagon 样本及其英文来源和中文展示溯源信息。"""

    id: str
    benchmark_id: str
    capability: Capability
    source_key: str
    selection_stratum: str
    input_sha256: str
    reference_sha256: str
    input_zh: str
    reference_zh: str | None
    input_zh_sha256: str
    reference_zh_sha256: str | None
    translation_version: str
    option_order: tuple[str, ...] | None = None


def _required_string(payload: dict[str, object], field: str) -> str:
    """读取一个非空字符串字段，统一向调用方暴露可诊断的清单错误。

    Args:
        payload: 单条 JSON 清单记录的原始字段映射。
        field: 必须存在且不能为空的字段名。

    Returns:
        去除首尾空白后的字段值。

    Raises:
        ValueError: 字段缺失、类型不是字符串或只包含空白字符时抛出。
    """
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest sample is missing {field}")
    return value.strip()


def _optional_string(payload: dict[str, object], field: str) -> str | None:
    """读取允许为空的翻译字段，并保持缺省值与显式空值的同一语义。

    Args:
        payload: 单条 JSON 清单记录的原始字段映射。
        field: 允许为 ``null`` 的中文参考翻译字段名。

    Returns:
        非空字符串或 ``None``，不会把空字符串伪装成已提供翻译。

    Raises:
        ValueError: 字段类型不合法或给出空白字符串时抛出。
    """
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest sample is missing {field}")
    return value.strip()


def _translation_digest(value: str) -> str:
    """计算展示翻译的 UTF-8 SHA-256，以验证其未在清单外被静默改写。

    Args:
        value: 非空的中文展示文本。

    Returns:
        使用小写十六进制表示的 SHA-256 摘要。
    """
    return sha256(value.encode("utf-8")).hexdigest()


def _validate_sha256(value: str, field: str) -> str:
    """确认字段是小写 SHA-256 摘要，避免不可复现或格式不一致的溯源记录。

    Args:
        value: 已读取的摘要字段值。
        field: 用于生成可诊断错误的字段名。

    Returns:
        原样返回已经验证的摘要字符串。

    Raises:
        ValueError: 值不符合 64 位小写十六进制 SHA-256 格式时抛出。
    """
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"manifest sample has invalid {field}")
    return value


def _parse_option_order(payload: dict[str, object]) -> tuple[str, ...] | None:
    """将 JSON 选项顺序转换为不可变元组，保留后续 TruthfulQA 审计所需顺序。

    Args:
        payload: 单条 JSON 清单记录的原始字段映射。

    Returns:
        未提供时返回 ``None``，否则返回仅含非空字符串的固定选项顺序。

    Raises:
        ValueError: 选项顺序不是 JSON 字符串数组或包含空值时抛出。
    """
    value = payload.get("option_order")
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("manifest sample has invalid option_order")
    return tuple(value)


def _parse_manifest_row(payload: object) -> HexagonSampleSpec:
    """解析并校验单条 JSON 样本记录的类型、必填字段和翻译摘要。

    Args:
        payload: 从 ``samples`` 数组读取的单条未知 JSON 数据。

    Returns:
        所有字段类型已收窄、翻译摘要已复算的不可变样本规格。

    Raises:
        ValueError: 记录不是对象、字段不完整、能力值无效或摘要与翻译不一致时抛出。
    """
    if not isinstance(payload, dict):
        raise ValueError("manifest sample must be an object")
    capability_name = _required_string(payload, "capability")
    try:
        capability = Capability(capability_name)
    except ValueError as exc:
        raise ValueError(f"manifest sample has invalid capability: {capability_name}") from exc
    input_zh = _required_string(payload, "input_zh")
    reference_zh = _optional_string(payload, "reference_zh")
    input_zh_sha256 = _validate_sha256(
        _required_string(payload, "input_zh_sha256"), "input_zh_sha256"
    )
    reference_zh_sha256 = _optional_string(payload, "reference_zh_sha256")
    if reference_zh_sha256 is not None:
        reference_zh_sha256 = _validate_sha256(reference_zh_sha256, "reference_zh_sha256")
    # 翻译摘要在读取边界立即复算，阻止仅修改展示文本却保持旧溯源哈希的情况。
    if input_zh_sha256 != _translation_digest(input_zh):
        raise ValueError("manifest sample input_zh SHA-256 mismatch")
    if reference_zh is None and reference_zh_sha256 is not None:
        raise ValueError("manifest sample reference_zh SHA-256 requires reference_zh")
    if reference_zh is not None and reference_zh_sha256 != _translation_digest(reference_zh):
        raise ValueError("manifest sample reference_zh SHA-256 mismatch")
    return HexagonSampleSpec(
        id=_required_string(payload, "id"),
        benchmark_id=_required_string(payload, "benchmark_id"),
        capability=capability,
        source_key=_required_string(payload, "source_key"),
        selection_stratum=_required_string(payload, "selection_stratum"),
        input_sha256=_validate_sha256(_required_string(payload, "input_sha256"), "input_sha256"),
        reference_sha256=_validate_sha256(
            _required_string(payload, "reference_sha256"), "reference_sha256"
        ),
        input_zh=input_zh,
        reference_zh=reference_zh,
        input_zh_sha256=input_zh_sha256,
        reference_zh_sha256=reference_zh_sha256,
        translation_version=_required_string(payload, "translation_version"),
        option_order=_parse_option_order(payload),
    )


def _validate_strata(rows: tuple[HexagonSampleSpec, ...]) -> None:
    """校验七个来源的精确分层集合及可由 source_key 复核的层级归属。

    Args:
        rows: 已完成单条字段校验的全部冻结样本。

    Raises:
        ValueError: 分层缺失、重复、超出固定范围或来源键与层级不一致时抛出。
    """
    for benchmark_id, expected in _EXPECTED_STRATA.items():
        selected = [row for row in rows if row.benchmark_id == benchmark_id]
        strata = {row.selection_stratum for row in selected}
        if strata != expected:
            raise ValueError(f"manifest sample strata mismatch for {benchmark_id}")
        for row in selected:
            if not _source_key_matches_stratum(row):
                raise ValueError(f"manifest sample source_key does not match stratum: {row.id}")


def _source_key_matches_stratum(row: HexagonSampleSpec) -> bool:
    """复核来源键中可表达的分层前缀，防止选择层级字段与来源记录脱节。

    Args:
        row: 已通过基本字段校验的冻结样本规格。

    Returns:
        不含可验证前缀的来源返回 ``True``，其余来源必须与选择层级一致。
    """
    if row.benchmark_id == "hexagon-mmlu":
        source_key_pattern = rf"{re.escape(row.selection_stratum)}:[1-9]\d*"
        return re.fullmatch(source_key_pattern, row.source_key) is not None
    if row.benchmark_id == "hexagon-ifeval":
        return row.source_key == _IFEVAL_SOURCE_KEYS[row.selection_stratum]
    if row.benchmark_id == "hexagon-bbh":
        return row.source_key.startswith(f"{row.selection_stratum}:")
    if row.benchmark_id == "hexagon-humaneval":
        return row.source_key == row.selection_stratum
    if row.benchmark_id == "hexagon-bbq":
        category, _, _ = row.selection_stratum.partition("/")
        return row.source_key.startswith(f"{category}:")
    return True


def _validate_manifest(rows: tuple[HexagonSampleSpec, ...]) -> None:
    """验证固定清单的总量、来源切片、能力映射和安全题目选项排列。

    Args:
        rows: 所有已解析且完成单行摘要校验的固定样本规格。

    Raises:
        ValueError: 样本数量、ID、来源、能力、分层或选项顺序偏离固定协议时抛出。
    """
    if len(rows) != 30 or len({row.id for row in rows}) != 30:
        raise ValueError("manifest must contain exactly 30 unique sample IDs")
    selectors = [(row.benchmark_id, row.source_key) for row in rows]
    if len(selectors) != len(set(selectors)):
        raise ValueError("manifest must contain unique source selectors per benchmark")
    benchmark_counts = Counter(row.benchmark_id for row in rows)
    if benchmark_counts != _EXPECTED_COUNTS:
        raise ValueError("manifest source slice counts do not match Hexagon protocol")
    capability_counts = Counter(row.capability for row in rows)
    if capability_counts != {capability: 5 for capability in Capability}:
        raise ValueError("manifest capability counts do not match Hexagon protocol")
    # 每个来源的能力映射是 Suite 评分画像的基础，不能仅依赖总数相等而允许错配。
    if any(_EXPECTED_CAPABILITIES.get(row.benchmark_id) != row.capability for row in rows):
        raise ValueError("manifest sample capability does not match benchmark")
    _validate_strata(rows)
    truthfulqa_orders = tuple(
        row.option_order for row in rows if row.benchmark_id == "hexagon-truthfulqa"
    )
    if truthfulqa_orders != _TRUTHFULQA_OPTION_ORDERS:
        raise ValueError("manifest TruthfulQA option_order must alternate AB and BA")
    if any(
        row.option_order is not None for row in rows if row.benchmark_id != "hexagon-truthfulqa"
    ):
        raise ValueError("manifest option_order is only allowed for TruthfulQA")


def load_hexagon_manifest(path: Path) -> tuple[HexagonSampleSpec, ...]:
    """读取并验证固定选择清单，拒绝缺字段、重复 ID 和空翻译。

    Args:
        path: 包含协议版本和 ``samples`` 数组的 UTF-8 JSON 清单路径。

    Returns:
        通过全部来源、翻译和固定分层校验的不可变样本规格元组。

    Raises:
        ValueError: JSON 结构、协议版本或任一样本不符合 Hexagon 固定清单契约时抛出。
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != "1.2.0":
        raise ValueError("manifest version must be 1.2.0")
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise ValueError("manifest samples must be a list")
    rows = tuple(_parse_manifest_row(item) for item in samples)
    _validate_manifest(rows)
    return rows


def hexagon_manifest() -> tuple[HexagonSampleSpec, ...]:
    """加载随包发布的 Hexagon v1 固定清单，供数据准备和执行层共享使用。

    Returns:
        已验证的 30 条固定样本规格，保持清单中的稳定顺序。

    Raises:
        FileNotFoundError: 发布包未包含固定 JSON 清单时抛出，阻止静默使用未冻结样本。
        ValueError: 包内清单不满足 Hexagon v1 协议时抛出。
    """
    manifest_path = Path(files("evalhub.datasets").joinpath("manifests/hexagon_v1.json"))
    return load_hexagon_manifest(manifest_path)
