"""从七个固定官方来源重建 EvalHub Hexagon v1 选择清单。"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

# 直接运行仓库脚本时优先使用同一工作树的 ``src``，避免误用其他分支的可编辑安装。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evalhub.datasets.hexagon_sources import (  # noqa: E402
    NormalizedSourceRow,
    hexagon_source_specs,
    load_hexagon_source_rows,
    parse_truthfulqa_rows,
    prepare_hexagon_dataset,
    select_keys,
)

SEED = "evalhub-hexagon-v1"
TRANSLATION_VERSION = "evalhub-zh-v1"
MMLU_STRATA = (
    "abstract_algebra",
    "anatomy",
    "business_ethics",
    "college_computer_science",
    "econometrics",
    "high_school_world_history",
    "international_law",
    "machine_learning",
    "professional_medicine",
    "sociology",
)
IFEVAL_SELECTIONS = (
    ("32", "punctuation:no_comma"),
    ("1759", "detectable_content:postscript"),
    ("2829", "startend:quotation"),
    ("321", "detectable_format:json_format"),
    ("3221", "detectable_content:number_placeholders"),
    ("2832", "detectable_format:number_bullet_lists"),
    ("2253", "detectable_format:number_highlighted_sections"),
    ("2925", "detectable_format:multiple_sections"),
    ("1551", "detectable_format:title"),
    ("1659", "startend:end_checker"),
)
BBH_STRATA = (
    "boolean_expressions",
    "causal_judgement",
    "date_understanding",
    "disambiguation_qa",
    "formal_fallacies",
    "logical_deduction_five_objects",
    "multistep_arithmetic_two",
    "object_counting",
    "temporal_sequences",
    "tracking_shuffled_objects_five_objects",
)
HUMANEVAL_TASK_IDS = (
    "HumanEval/126",
    "HumanEval/84",
    "HumanEval/108",
    "HumanEval/30",
    "HumanEval/24",
    "HumanEval/54",
    "HumanEval/158",
    "HumanEval/131",
    "HumanEval/123",
    "HumanEval/63",
)
TRUTHFULQA_STRATA = (
    "Misconceptions",
    "Health",
    "Conspiracies",
    "Stereotypes",
    "Superstitions",
)
TRUTHFULQA_ORDERS = (("A", "B"), ("B", "A"), ("A", "B"), ("B", "A"), ("A", "B"))
BBQ_STRATA = (
    "Age/ambig",
    "Disability_status/disambig",
    "Gender_identity/ambig",
    "Race_ethnicity/disambig",
    "Religion/ambig",
)
CAPABILITIES = {
    "hexagon-mmlu": "knowledge",
    "hexagon-ifeval": "instruction_following",
    "hexagon-gsm8k": "mathematics",
    "hexagon-bbh": "reasoning",
    "hexagon-humaneval": "coding",
    "hexagon-truthfulqa": "safety_trust",
    "hexagon-bbq": "safety_trust",
}


def _digest(value: str) -> str:
    """计算清单正文使用的 UTF-8 SHA-256 小写十六进制摘要。

    Args:
        value: 官方英文正文或 EvalHub 中文辅助翻译。

    Returns:
        可写入清单溯源字段的 64 位摘要。
    """
    return sha256(value.encode()).hexdigest()


def _one_per_stratum(
    rows: Mapping[str, NormalizedSourceRow],
    strata: Sequence[str],
    *,
    field: str,
) -> list[tuple[str, str, tuple[str, ...] | None]]:
    """按元数据字段分层哈希选择一条记录，并保留调用方给定的层级顺序。

    Args:
        rows: 某一官方来源解析出的全部规范化记录。
        strata: 协议中固定且有序的分层值。
        field: 规范化来源元数据中用于匹配层级的字段名。

    Returns:
        由层级、选中来源键和空选项顺序组成的选择计划。

    Raises:
        ValueError: 任一固定分层没有可供选择的唯一来源键时抛出。
    """
    selected: list[tuple[str, str, tuple[str, ...] | None]] = []
    for stratum in strata:
        candidates = [key for key, row in rows.items() if row.source_metadata.get(field) == stratum]
        source_key = select_keys(candidates, count=1, seed=SEED)[0]
        selected.append((stratum, source_key, None))
    return selected


def _bbq_plan(
    rows: Mapping[str, NormalizedSourceRow],
) -> list[tuple[str, str, tuple[str, ...] | None]]:
    """按 BBQ 类别和上下文条件组合分层选择固定五条安全可信样本。

    Args:
        rows: BBQ 归档中的全部规范化记录。

    Returns:
        保持协议层级顺序的五条来源键选择计划。

    Raises:
        ValueError: 任一类别/条件层级没有候选记录时抛出。
    """
    selected: list[tuple[str, str, tuple[str, ...] | None]] = []
    for stratum in BBQ_STRATA:
        category, condition = stratum.split("/", maxsplit=1)
        candidates = [
            key
            for key, row in rows.items()
            if row.source_metadata.get("category") == category
            and row.source_metadata.get("context_condition") == condition
        ]
        selected.append((stratum, select_keys(candidates, count=1, seed=SEED)[0], None))
    return selected


def _ifeval_plan(
    rows: Mapping[str, NormalizedSourceRow],
) -> list[tuple[str, str, tuple[str, ...] | None]]:
    """核对十个固定 IFEval 键各自只含指定官方规则，并建立选择计划。

    Args:
        rows: 固定 IFEval JSONL 的全部规范化记录。

    Returns:
        按协议给定顺序排列的规则层级和十进制来源键。

    Raises:
        ValueError: 固定键缺失或其规则列表不等于唯一指定规则时抛出。
    """
    selected: list[tuple[str, str, tuple[str, ...] | None]] = []
    for source_key, rule in IFEVAL_SELECTIONS:
        row = rows.get(source_key)
        if row is None:
            raise ValueError(f"missing IFEval source key: {source_key}")
        if row.source_metadata.get("instruction_id_list") != [rule]:
            raise ValueError(f"IFEval source key {source_key} must contain exactly rule {rule}")
        selected.append((rule, source_key, None))
    return selected


def _humaneval_plan(
    rows: Mapping[str, NormalizedSourceRow],
) -> list[tuple[str, str, tuple[str, ...] | None]]:
    """按协议固定任务 ID 顺序建立 HumanEval 发现选择计划。

    Args:
        rows: 固定 HumanEval gzip 中的全部规范化任务。

    Returns:
        以任务 ID 同时作为层级和来源键的十条选择计划。

    Raises:
        ValueError: 任一协议固定任务 ID 不在官方来源中时抛出。
    """
    missing = [source_key for source_key in HUMANEVAL_TASK_IDS if source_key not in rows]
    if missing:
        raise ValueError(f"missing HumanEval source keys: {', '.join(missing)}")
    return [(source_key, source_key, None) for source_key in HUMANEVAL_TASK_IDS]


def _truthfulqa_plan(
    rows: Mapping[str, NormalizedSourceRow],
) -> list[tuple[str, str, tuple[str, ...] | None]]:
    """按五个 TruthfulQA 类别选择记录并交替冻结正确答案选项位置。

    Args:
        rows: 使用默认 AB 排列解析的全部 TruthfulQA 记录。

    Returns:
        按类别顺序附带 ``AB/BA`` 选项排列的五条选择计划。

    Raises:
        ValueError: 任一固定类别没有候选记录时抛出。
    """
    selected = _one_per_stratum(rows, TRUTHFULQA_STRATA, field="category")
    return [
        (stratum, source_key, order)
        for (stratum, source_key, _), order in zip(selected, TRUTHFULQA_ORDERS, strict=True)
    ]


def _source_plans(
    rows_by_benchmark: Mapping[str, Mapping[str, NormalizedSourceRow]],
) -> dict[str, list[tuple[str, str, tuple[str, ...] | None]]]:
    """对七个完整来源应用协议精确分层和固定键规则，形成 60 条选择计划。

    Args:
        rows_by_benchmark: 以 Hexagon ID 索引的七源规范化记录映射。

    Returns:
        每个来源对应的有序层级、来源键和可选选项排列列表。

    Raises:
        ValueError: 任一来源缺少固定层级、键或 IFEval 精确规则时抛出。
    """
    mmlu = _one_per_stratum(rows_by_benchmark["hexagon-mmlu"], MMLU_STRATA, field="subject")
    gsm_keys = select_keys(rows_by_benchmark["hexagon-gsm8k"], count=10, seed=SEED)
    gsm = [("test", source_key, None) for source_key in gsm_keys]
    bbh = _one_per_stratum(rows_by_benchmark["hexagon-bbh"], BBH_STRATA, field="task")
    # 各来源独立选择，避免跨来源来源键碰撞或遍历顺序影响固定切片。
    return {
        "hexagon-mmlu": mmlu,
        "hexagon-ifeval": _ifeval_plan(rows_by_benchmark["hexagon-ifeval"]),
        "hexagon-gsm8k": gsm,
        "hexagon-bbh": bbh,
        "hexagon-humaneval": _humaneval_plan(rows_by_benchmark["hexagon-humaneval"]),
        "hexagon-truthfulqa": _truthfulqa_plan(rows_by_benchmark["hexagon-truthfulqa"]),
        "hexagon-bbq": _bbq_plan(rows_by_benchmark["hexagon-bbq"]),
    }


def build_manifest_rows(root: Path) -> list[dict[str, object]]:
    """通过现有固定资产准备边界加载七源，并构造 60 条英文发现记录。

    Args:
        root: 固定资产缓存相对的项目根目录。

    Returns:
        含英文 ``input/reference`` 的临时发现记录；写入清单前必须合并翻译并移除正文。

    Raises:
        ValueError: 固定文件摘要、来源格式、层级或键不符合 Hexagon v1 协议时抛出。
        OSError: 固定来源下载或读取失败时保留底层错误。
    """
    specs = hexagon_source_specs()
    paths = {benchmark_id: prepare_hexagon_dataset(benchmark_id, root) for benchmark_id in specs}
    rows_by_benchmark = {
        benchmark_id: load_hexagon_source_rows(benchmark_id, paths[benchmark_id])
        for benchmark_id in specs
    }
    plans = _source_plans(rows_by_benchmark)
    # TruthfulQA 英文输入摘要依赖已选择记录的冻结选项排列，确定计划后重新规范化该来源。
    truthfulqa_orders = {
        source_key: order
        for _, source_key, order in plans["hexagon-truthfulqa"]
        if order is not None
    }
    rows_by_benchmark["hexagon-truthfulqa"] = parse_truthfulqa_rows(
        paths["hexagon-truthfulqa"], truthfulqa_orders
    )
    return _discovered_rows(rows_by_benchmark, plans)


def _discovered_rows(
    rows_by_benchmark: Mapping[str, Mapping[str, NormalizedSourceRow]],
    plans: Mapping[str, Sequence[tuple[str, str, tuple[str, ...] | None]]],
) -> list[dict[str, object]]:
    """把七源选择计划转换为带稳定逻辑 ID 的内部清单发现记录。

    Args:
        rows_by_benchmark: 七源全部规范化记录，TruthfulQA 已使用冻结选项顺序重建。
        plans: 按协议来源顺序和分层顺序排列的 60 条选择计划。

    Returns:
        可供翻译合并器计算摘要并剥离英文正文的有序字典列表。

    Raises:
        KeyError: 选择计划中的来源键未在规范化记录中命中时抛出。
    """
    output: list[dict[str, object]] = []
    for benchmark_id in CAPABILITIES:
        short_name = benchmark_id.removeprefix("hexagon-")
        for index, (stratum, source_key, option_order) in enumerate(plans[benchmark_id], start=1):
            row = rows_by_benchmark[benchmark_id][source_key]
            output.append(
                {
                    "id": f"hexagon_{short_name}_{index:02d}",
                    "benchmark_id": benchmark_id,
                    "capability": CAPABILITIES[benchmark_id],
                    "source_key": source_key,
                    "selection_stratum": stratum,
                    "input": row.input,
                    "reference": row.reference,
                    "option_order": list(option_order) if option_order is not None else None,
                }
            )
    return output


def _existing_translations(path: Path) -> dict[tuple[str, str], Mapping[str, object]]:
    """读取既有清单并按来源切片和来源键索引可复用中文翻译。

    Args:
        path: 当前随包发布、将被确定性重建的清单路径。

    Returns:
        以 ``(benchmark_id, source_key)`` 为键的既有样本对象映射。

    Raises:
        FileNotFoundError: 初始翻译尚未编制时抛出，要求先提供完整人工译文。
        ValueError: 清单 JSON 结构无效或包含重复翻译选择器时抛出。
    """
    if not path.exists():
        raise FileNotFoundError(f"translation manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("samples"), list):
        raise ValueError("translation manifest samples must be a list")
    translations: dict[tuple[str, str], Mapping[str, object]] = {}
    for sample in payload["samples"]:
        if not isinstance(sample, dict):
            raise ValueError("translation manifest sample must be an object")
        benchmark_id = sample.get("benchmark_id")
        source_key = sample.get("source_key")
        if not isinstance(benchmark_id, str) or not isinstance(source_key, str):
            raise ValueError("translation manifest sample is missing its selector")
        selector = (benchmark_id, source_key)
        if selector in translations:
            raise ValueError(f"duplicate preserved translation selector: {selector}")
        translations[selector] = sample
    return translations


def _preserved_translation(
    sample: Mapping[str, object], selector: tuple[str, str]
) -> tuple[str, str | None]:
    """验证一条既有翻译及保存摘要，防止重建传播被静默改写的中文正文。

    Args:
        sample: 与新选择器匹配的既有清单样本对象。
        selector: 当前来源切片和来源键，用于诊断错误。

    Returns:
        已通过摘要复算且规范化首尾空白的中文输入和可选中文参考答案。

    Raises:
        ValueError: 翻译缺失、版本错误或任一保存摘要与正文不一致时抛出。
    """
    input_zh = sample.get("input_zh")
    reference_zh = sample.get("reference_zh")
    if not isinstance(input_zh, str) or not input_zh:
        raise ValueError(f"missing preserved input_zh for {selector}")
    if reference_zh is not None and (not isinstance(reference_zh, str) or not reference_zh):
        raise ValueError(f"invalid preserved reference_zh for {selector}")
    if sample.get("translation_version") != TRANSLATION_VERSION:
        raise ValueError(f"invalid preserved translation version for {selector}")
    if sample.get("input_zh_sha256") != _digest(input_zh):
        raise ValueError(f"preserved input_zh SHA-256 mismatch for {selector}")
    expected_reference_digest = None if reference_zh is None else _digest(reference_zh)
    if sample.get("reference_zh_sha256") != expected_reference_digest:
        raise ValueError(f"preserved reference_zh SHA-256 mismatch for {selector}")
    # 清单读取边界会去除首尾空白；构建时采用相同规范化，确保写入摘要对应实际字段。
    normalized_reference = None if reference_zh is None else reference_zh.strip()
    return input_zh.strip(), normalized_reference


def merge_translations(
    rows: Sequence[Mapping[str, object]], path: Path
) -> list[dict[str, object]]:
    """合并同键人工翻译，重算四类摘要，并从提交清单中移除英文正文。

    Args:
        rows: ``build_manifest_rows`` 生成的有序英文发现记录。
        path: 提供现有 ``evalhub-zh-v1`` 翻译的目标清单路径。

    Returns:
        只含选择器、摘要、翻译和协议元数据的可提交清单样本列表。

    Raises:
        FileNotFoundError: 目标路径尚无人工翻译清单时抛出。
        ValueError: 选择器缺翻译、翻译摘要失配或内部英文记录类型无效时抛出。
    """
    translations = _existing_translations(path)
    merged: list[dict[str, object]] = []
    for row in rows:
        benchmark_id = row.get("benchmark_id")
        source_key = row.get("source_key")
        input_text = row.get("input")
        reference = row.get("reference")
        text_fields = (benchmark_id, source_key, input_text, reference)
        if not all(isinstance(value, str) for value in text_fields):
            raise ValueError("discovered manifest row has invalid text fields")
        selector = (benchmark_id, source_key)
        existing = translations.get(selector)
        if existing is None:
            raise ValueError(f"missing preserved translation for {selector}")
        input_zh, reference_zh = _preserved_translation(existing, selector)
        final = {key: value for key, value in row.items() if key not in {"input", "reference"}}
        final.update(
            {
                "input_sha256": _digest(input_text),
                "reference_sha256": _digest(reference),
                "input_zh": input_zh,
                "reference_zh": reference_zh,
                "input_zh_sha256": _digest(input_zh),
                "reference_zh_sha256": None if reference_zh is None else _digest(reference_zh),
                "translation_version": TRANSLATION_VERSION,
            }
        )
        merged.append(final)
    return merged


def write_manifest(path: Path, samples: Sequence[Mapping[str, object]]) -> None:
    """以排序键、固定缩进和结尾换行写出字节稳定的 UTF-8 Hexagon 清单。

    Args:
        path: 要原子重建内容的包内 JSON 清单路径。
        samples: 已合并翻译并移除英文正文的有序样本记录。

    Raises:
        OSError: 目标目录创建或文件写入失败时保留底层错误。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": "1.0.0", "samples": list(samples)}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    """构造清单重建命令行解析器，并集中声明根目录与输出路径参数。

    Returns:
        可解析 ``--root`` 和必填 ``--output`` 的参数解析器。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="fixed-source cache root")
    parser.add_argument("--output", required=True, help="existing translated manifest path")
    return parser


def main() -> int:
    """从固定官方缓存重建清单，并拒绝缺失或失配的人工翻译。

    Returns:
        所有来源、选择器、摘要和翻译校验通过并写入时返回零。

    Raises:
        FileNotFoundError: 固定来源或人工翻译清单不可用时抛出。
        ValueError: 固定摘要、来源格式、选择层级或翻译溯源不符合协议时抛出。
    """
    args = _parser().parse_args()
    output = Path(args.output)
    rows = build_manifest_rows(Path(args.root))
    write_manifest(output, merge_translations(rows, output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
