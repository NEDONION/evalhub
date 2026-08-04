"""验证 Hexagon 固定选择清单的来源与双语溯源契约。"""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from evalhub.datasets.hexagon_manifest import hexagon_manifest, load_hexagon_manifest


def _digest(value: str) -> str:
    """计算测试清单展示文本的摘要，构造可通过真实解析器校验的本地夹具。

    Args:
        value: 要写入清单的 UTF-8 文本。

    Returns:
        与生产清单格式一致的小写 SHA-256 十六进制摘要。
    """
    return sha256(value.encode("utf-8")).hexdigest()


def _sample(
    index: int,
    benchmark_id: str,
    capability: str,
    stratum: str,
    source_key: str,
    option_order: list[str] | None = None,
) -> dict[str, object]:
    """构造一条满足固定字段契约的本地清单样本，供拒绝路径测试精确变更单个字段。

    Args:
        index: 用于生成唯一逻辑 ID 和中文展示文本的一基序号。
        benchmark_id: 固定 Hexagon 来源切片 ID。
        capability: 与来源切片对应的能力枚举字符串值。
        stratum: 本条记录所属的固定选择层级。
        source_key: 官方来源中可追溯到具体原始样本的键。
        option_order: TruthfulQA 使用的固定二选一展示顺序，其他来源保持 ``None``。

    Returns:
        可直接序列化为 JSON 的完整单条清单记录。
    """
    input_zh = f"中文题目 {index}"
    reference_zh = f"中文答案 {index}"
    return {
        "id": f"hexagon_{index:02d}",
        "benchmark_id": benchmark_id,
        "capability": capability,
        "source_key": source_key,
        "selection_stratum": stratum,
        "input_sha256": _digest(f"English input {index}"),
        "reference_sha256": _digest(f"English reference {index}"),
        "input_zh": input_zh,
        "reference_zh": reference_zh,
        "input_zh_sha256": _digest(input_zh),
        "reference_zh_sha256": _digest(reference_zh),
        "translation_version": "evalhub-zh-v1",
        "option_order": option_order,
    }


def _manifest_payload() -> dict[str, object]:
    """构造覆盖全部七个固定分层的有效 60 行清单，避免测试依赖公开网络来源。

    Returns:
        使用字面量来源层级和 IFEval 键映射的完整 Hexagon v1 JSON 对象。
    """
    samples: list[dict[str, object]] = []
    source_groups = (
        (
            "hexagon-mmlu",
            "knowledge",
            (
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
            ),
        ),
        (
            "hexagon-gsm8k",
            "mathematics",
            ("test",) * 10,
        ),
        (
            "hexagon-bbh",
            "reasoning",
            (
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
            ),
        ),
        (
            "hexagon-humaneval",
            "coding",
            (
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
            ),
        ),
        (
            "hexagon-truthfulqa",
            "safety_trust",
            ("Misconceptions", "Health", "Conspiracies", "Stereotypes", "Superstitions"),
        ),
        (
            "hexagon-bbq",
            "safety_trust",
            (
                "Age/ambig",
                "Disability_status/disambig",
                "Gender_identity/ambig",
                "Race_ethnicity/disambig",
                "Religion/ambig",
            ),
        ),
    )
    # 除 IFEval 外，各来源键可由固定层级直接派生，保证每项只验证一个来源契约。
    for benchmark_id, capability, strata in source_groups:
        for stratum in strata:
            index = len(samples) + 1
            source_key = _source_key(benchmark_id, stratum, index)
            option_order = None
            if benchmark_id == "hexagon-truthfulqa":
                option_order = ["A", "B"] if index % 2 else ["B", "A"]
            samples.append(
                _sample(index, benchmark_id, capability, stratum, source_key, option_order)
            )
    # IFEval 的官方整数键与规则名并非可推导关系，故以协议中的字面量映射覆盖。
    for stratum, source_key in (
        ("punctuation:no_comma", "32"),
        ("detectable_content:postscript", "1759"),
        ("startend:quotation", "2829"),
        ("detectable_format:json_format", "321"),
        ("detectable_content:number_placeholders", "3221"),
        ("detectable_format:number_bullet_lists", "2832"),
        ("detectable_format:number_highlighted_sections", "2253"),
        ("detectable_format:multiple_sections", "2925"),
        ("detectable_format:title", "1551"),
        ("startend:end_checker", "1659"),
    ):
        index = len(samples) + 1
        samples.append(
            _sample(index, "hexagon-ifeval", "instruction_following", stratum, source_key)
        )
    return {"version": "1.0.0", "samples": samples}


def _source_key(benchmark_id: str, stratum: str, index: int) -> str:
    """为本地完整清单生成与当前来源键协议一致的可验证键。

    Args:
        benchmark_id: 需要生成来源键的固定 Hexagon 切片 ID。
        stratum: 此记录所属的固定选择层级。
        index: GSM8K 与 TruthfulQA 来源键所需的一基行号。

    Returns:
        能通过对应来源层级交叉校验的最小来源键。
    """
    if benchmark_id == "hexagon-gsm8k":
        return f"test.jsonl:{index}"
    if benchmark_id == "hexagon-humaneval":
        return stratum
    if benchmark_id == "hexagon-truthfulqa":
        return f"TruthfulQA.csv:{index}"
    if benchmark_id == "hexagon-bbq":
        return f"{stratum.partition('/')[0]}:{index}"
    return f"{stratum}:{index}"


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    """将内存中的夹具写为 UTF-8 JSON 文件，供公开加载接口端到端验证。

    Args:
        path: 临时目录中的目标清单文件路径。
        payload: 已构造的完整或经单字段变更后的 JSON 清单对象。
    """
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_hexagon_manifest_requires_complete_bilingual_provenance(tmp_path: Path) -> None:
    """缺少固定样本必填溯源字段时，清单读取必须明确拒绝。"""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"version": "1.0.0", "samples": [{"id": "broken"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest sample is missing"):
        load_hexagon_manifest(path)


def test_hexagon_manifest_accepts_fixed_ifeval_key_stratum_mapping(tmp_path: Path) -> None:
    """完整清单必须接受协议字面量中每个 IFEval 规则与官方整数键的对应关系。"""
    path = tmp_path / "manifest.json"
    _write_manifest(path, _manifest_payload())

    assert len(load_hexagon_manifest(path)) == 60


def test_hexagon_manifest_rejects_changed_translation_with_stale_digest(tmp_path: Path) -> None:
    """展示翻译变更而摘要未同步时，清单必须拒绝错误的双语溯源记录。"""
    path = tmp_path / "manifest.json"
    payload = _manifest_payload()
    samples = payload["samples"]
    assert isinstance(samples, list)
    samples[0]["input_zh"] = "被篡改的中文题目"
    _write_manifest(path, payload)

    with pytest.raises(ValueError, match="input_zh SHA-256 mismatch"):
        load_hexagon_manifest(path)


@pytest.mark.parametrize("source_key", ("other_abstract_algebra:1", "abstract_algebra:"))
def test_hexagon_manifest_rejects_mmlu_source_key_without_exact_stratum_prefix(
    tmp_path: Path, source_key: str
) -> None:
    """MMLU 来源键必须是精确的学科名加一基行号，不能接受包含学科名的其他前缀。

    Args:
        tmp_path: pytest 提供的隔离临时目录。
        source_key: 应被拒绝的错误前缀或缺失行号来源键。
    """
    path = tmp_path / "manifest.json"
    payload = _manifest_payload()
    samples = payload["samples"]
    assert isinstance(samples, list)
    samples[0]["source_key"] = source_key
    _write_manifest(path, payload)

    with pytest.raises(ValueError, match="source_key does not match stratum"):
        load_hexagon_manifest(path)


def test_hexagon_manifest_rejects_duplicate_source_selectors(tmp_path: Path) -> None:
    """同一来源切片中的选择器必须唯一，避免一条官方记录被重复计入能力分。"""
    path = tmp_path / "manifest.json"
    payload = _manifest_payload()
    samples = payload["samples"]
    assert isinstance(samples, list)
    samples[11]["source_key"] = samples[10]["source_key"]
    _write_manifest(path, payload)

    with pytest.raises(ValueError, match="unique source selectors"):
        load_hexagon_manifest(path)


def test_packaged_hexagon_manifest_contains_sixty_verified_translations() -> None:
    """发布包必须携带冻结的 60 行清单，且每条中文辅助翻译均非空并通过摘要校验。"""
    rows = hexagon_manifest()

    assert len(rows) == 60
    assert all(row.input_zh.strip() for row in rows)
    assert all(row.translation_version == "evalhub-zh-v1" for row in rows)
