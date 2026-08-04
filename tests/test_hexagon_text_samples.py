"""验证 Hexagon 固定来源解析、选择和双语隔离边界。"""

import csv
import gzip
import io
import json
import subprocess
import sys
import tarfile
from hashlib import sha256
from pathlib import Path

import pytest

from evalhub.benchmarks.models import Capability
from evalhub.datasets.hexagon_manifest import HexagonSampleSpec
from evalhub.datasets.hexagon_sources import (
    parse_bbh_rows,
    parse_bbq_rows,
    parse_humaneval_rows,
    parse_ifeval_rows,
    parse_mmlu_rows,
    parse_truthfulqa_rows,
    select_keys,
)
from evalhub.datasets.loaders import load_hexagon_samples
from scripts.build_hexagon_manifest import merge_translations, write_manifest


def _digest(value: str) -> str:
    """计算测试记录的 UTF-8 SHA-256，与生产摘要门禁使用同一字节协议。

    Args:
        value: 需要写入测试清单摘要字段的英文或中文文本。

    Returns:
        小写十六进制 SHA-256 摘要。
    """
    return sha256(value.encode("utf-8")).hexdigest()


def _spec(
    *,
    benchmark_id: str,
    source_key: str,
    input_text: str,
    reference: str,
    input_zh: str = "中文题目",
    reference_zh: str | None = "中文答案",
    option_order: tuple[str, ...] | None = None,
) -> HexagonSampleSpec:
    """构造单条已冻结规格，用于隔离验证加载器而不依赖完整 60 行清单。

    Args:
        benchmark_id: 需要触发的 Hexagon 来源 ID。
        source_key: 应在本地来源夹具中唯一命中的选择器。
        input_text: 预期送入模型的完整英文规范化输入。
        reference: 预期参与评分或后续执行的官方英文参考值。
        input_zh: 只允许进入元数据的中文辅助翻译。
        reference_zh: 可选的中文参考答案展示文本。
        option_order: TruthfulQA 使用的固定二选一排列。

    Returns:
        摘要与正文一致的不可变 Hexagon 样本规格。
    """
    reference_zh_digest = None if reference_zh is None else _digest(reference_zh)
    return HexagonSampleSpec(
        id="hexagon_fixture_01",
        benchmark_id=benchmark_id,
        capability=Capability.MATHEMATICS,
        source_key=source_key,
        selection_stratum="test",
        input_sha256=_digest(input_text),
        reference_sha256=_digest(reference),
        input_zh=input_zh,
        reference_zh=reference_zh,
        input_zh_sha256=_digest(input_zh),
        reference_zh_sha256=reference_zh_digest,
        translation_version="evalhub-zh-v1",
        option_order=option_order,
    )


def _write_tar_member(path: Path, name: str, payload: bytes) -> None:
    """创建只含一个成员的 gzip tar 夹具，验证解析器无需落盘解压即可读取。

    Args:
        path: 要创建的临时归档文件。
        name: 归档内模拟官方仓库布局的成员路径。
        payload: 成员的原始 UTF-8 或 JSON 字节。
    """
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))


def test_fixed_hash_selection_is_independent_of_input_order() -> None:
    """固定种子选择必须仅由来源键决定，反转上游遍历顺序不能改变结果。"""
    keys = ["test.jsonl:3", "test.jsonl:1", "test.jsonl:2"]
    expected = select_keys(keys, count=2, seed="evalhub-hexagon-v1")

    assert select_keys(list(reversed(keys)), count=2, seed="evalhub-hexagon-v1") == expected


def test_fixed_hash_selection_rejects_too_few_unique_keys() -> None:
    """重复来源键不能充当多个候选，唯一键不足时必须在构建清单前失败。"""
    with pytest.raises(ValueError, match="required 2, found 1"):
        select_keys(["same", "same"], count=2, seed="evalhub-hexagon-v1")


def test_text_sample_keeps_translation_out_of_model_input(tmp_path: Path) -> None:
    """Hexagon GSM8K 必须复用英文提示格式，并把中文翻译严格限制在元数据。"""
    path = tmp_path / "data/raw/gsm8k/test.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"question": "Ignored?", "answer": "#### 1"},
        {"question": "How many?", "answer": "#### 7"},
    ]
    path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")
    prompt = (
        "Solve the following grade-school math problem. "
        "Return the final answer as a single number.\n\n"
        "Problem: How many?\n\nFinal answer:"
    )
    manifest = (
        _spec(
            benchmark_id="hexagon-gsm8k",
            source_key="test.jsonl:2",
            input_text=prompt,
            reference="7",
            input_zh="一共有多少？",
            reference_zh="7",
        ),
    )

    samples = load_hexagon_samples("hexagon-gsm8k", root=tmp_path, manifest=manifest)

    assert samples[0].input.endswith("Problem: How many?\n\nFinal answer:")
    assert "一共有多少" not in samples[0].input
    assert samples[0].metadata["input_zh"] == "一共有多少？"
    assert samples[0].metadata["source_key"] == "test.jsonl:2"


def test_hexagon_loader_rejects_missing_selector(tmp_path: Path) -> None:
    """清单选择器未在已准备来源中命中时必须明确失败，不能跳题或改选其他记录。"""
    path = tmp_path / "data/raw/gsm8k/test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"question":"Only row","answer":"#### 1"}\n', encoding="utf-8")
    manifest = (
        _spec(
            benchmark_id="hexagon-gsm8k",
            source_key="test.jsonl:2",
            input_text="unused",
            reference="1",
        ),
    )

    with pytest.raises(ValueError, match="missing source selector: test.jsonl:2"):
        load_hexagon_samples("hexagon-gsm8k", root=tmp_path, manifest=manifest)


def test_hexagon_loader_rejects_changed_english_content(tmp_path: Path) -> None:
    """官方英文正文与冻结摘要不一致时必须在模型调用前阻断漂移来源。"""
    path = tmp_path / "data/raw/gsm8k/test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"question":"Changed","answer":"#### 1"}\n', encoding="utf-8")
    manifest = (
        _spec(
            benchmark_id="hexagon-gsm8k",
            source_key="test.jsonl:1",
            input_text="stale input",
            reference="1",
        ),
    )

    with pytest.raises(ValueError, match="input SHA-256 mismatch"):
        load_hexagon_samples("hexagon-gsm8k", root=tmp_path, manifest=manifest)


def test_mmlu_parser_reads_test_csv_from_pinned_archive(tmp_path: Path) -> None:
    """MMLU 解析器必须从固定归档读取一基行号、四个选项和官方答案字母。"""
    archive_path = tmp_path / "data.tar"
    csv_buffer = io.StringIO()
    csv.writer(csv_buffer).writerow(["Question?", "One", "Two", "Three", "Four", "C"])
    _write_tar_member(
        archive_path,
        "data/test/abstract_algebra_test.csv",
        csv_buffer.getvalue().encode("utf-8"),
    )

    rows = parse_mmlu_rows(archive_path)

    assert rows["abstract_algebra:1"].reference == "C"
    assert "A. One" in rows["abstract_algebra:1"].input
    assert rows["abstract_algebra:1"].source_metadata["subject"] == "abstract_algebra"


def test_bbh_parser_reads_archive_without_extracting(tmp_path: Path) -> None:
    """BBH 解析器必须直接读取归档成员，并保留任务名与官方目标而不创建解压目录。"""
    archive_path = tmp_path / "archive.tar.gz"
    payload = json.dumps({"examples": [{"input": "True or false?", "target": "True"}]})
    _write_tar_member(
        archive_path,
        "BIG-Bench-Hard-revision/bbh/boolean_expressions.json",
        payload.encode("utf-8"),
    )

    rows = parse_bbh_rows(archive_path)

    assert rows["boolean_expressions:1"].reference == "True"
    assert rows["boolean_expressions:1"].source_metadata["task"] == "boolean_expressions"
    assert list(tmp_path.iterdir()) == [archive_path]


def test_truthfulqa_parser_freezes_binary_option_order(tmp_path: Path) -> None:
    """TruthfulQA 二选一输入必须按清单顺序排列正确与错误答案并返回相应字母。"""
    path = tmp_path / "TruthfulQA.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["Question", "Best Answer", "Best Incorrect Answer", "Category"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Question": "What is true?",
                "Best Answer": "Correct",
                "Best Incorrect Answer": "Wrong",
                "Category": "Misconceptions",
            }
        )

    rows = parse_truthfulqa_rows(path, {"TruthfulQA.csv:1": ("B", "A")})

    assert "A. Wrong" in rows["TruthfulQA.csv:1"].input
    assert "B. Correct" in rows["TruthfulQA.csv:1"].input
    assert rows["TruthfulQA.csv:1"].reference == "B"


def test_bbq_parser_preserves_scoring_fields_and_rejects_duplicate_key(tmp_path: Path) -> None:
    """BBQ 必须保存官方评分字段，并拒绝同类别中重复 example_id 造成的歧义选择。"""
    archive_path = tmp_path / "archive.tar.gz"
    row = {
        "example_id": 7,
        "category": "Age",
        "context_condition": "ambig",
        "context": "Taylor met Lee.",
        "question": "Who was older?",
        "ans0": "Taylor",
        "ans1": "Lee",
        "ans2": "Unknown",
        "label": 2,
    }
    payload = "\n".join((json.dumps(row), json.dumps(row), ""))
    _write_tar_member(
        archive_path,
        "BBQ-revision/data/Age.jsonl",
        payload.encode("utf-8"),
    )

    with pytest.raises(ValueError, match="duplicate source key: Age:7"):
        parse_bbq_rows(archive_path)


def test_bbq_parser_preserves_context_question_answers_and_label(tmp_path: Path) -> None:
    """单条 BBQ 记录必须保留上下文、问题、三个选项、标签、条件和类别供评分审计。"""
    archive_path = tmp_path / "archive.tar.gz"
    row = {
        "example_id": 7,
        "category": "Age",
        "context_condition": "ambig",
        "context": "Taylor met Lee.",
        "question": "Who was older?",
        "ans0": "Taylor",
        "ans1": "Lee",
        "ans2": "Unknown",
        "label": 2,
    }
    _write_tar_member(
        archive_path,
        "BBQ-revision/data/Age.jsonl",
        f"{json.dumps(row)}\n".encode(),
    )

    parsed = parse_bbq_rows(archive_path)["Age:7"]

    assert parsed.reference == "C"
    assert parsed.source_metadata == row
    assert "Context: Taylor met Lee." in parsed.input


def test_ifeval_and_humaneval_discovery_preserves_official_metadata(tmp_path: Path) -> None:
    """IFEval 与 HumanEval 发现阶段必须保留后续规则评分和沙箱执行需要的官方字段。"""
    ifeval_path = tmp_path / "input_data.jsonl"
    ifeval_row = {
        "key": 32,
        "prompt": "Do not use commas.",
        "instruction_id_list": ["punctuation:no_comma"],
        "kwargs": [{}],
    }
    ifeval_path.write_text(f"{json.dumps(ifeval_row)}\n", encoding="utf-8")
    humaneval_path = tmp_path / "HumanEval.jsonl.gz"
    humaneval_row = {
        "task_id": "HumanEval/1",
        "prompt": "def one():\n",
        "canonical_solution": "    return 1\n",
        "test": "def check(candidate): assert candidate() == 1\n",
        "entry_point": "one",
    }
    with gzip.open(humaneval_path, "wt", encoding="utf-8") as stream:
        stream.write(f"{json.dumps(humaneval_row)}\n")

    ifeval = parse_ifeval_rows(ifeval_path)["32"]
    humaneval = parse_humaneval_rows(humaneval_path)["HumanEval/1"]

    assert ifeval.reference == ""
    assert ifeval.source_metadata["instruction_id_list"] == ["punctuation:no_comma"]
    assert humaneval.reference == "    return 1\n"
    assert humaneval.source_metadata["entry_point"] == "one"


def test_manifest_builder_preserves_translations_and_recomputes_all_digests(
    tmp_path: Path,
) -> None:
    """重建清单必须复用同键翻译、移除英文正文，并按当前四个正文值重算摘要。

    Args:
        tmp_path: pytest 提供的隔离目录，用于保存既有翻译清单。
    """
    path = tmp_path / "manifest.json"
    existing = {
        "version": "1.0.0",
        "samples": [
            {
                "benchmark_id": "hexagon-gsm8k",
                "source_key": "test.jsonl:1",
                "input_zh": "\n一道题\n",
                "reference_zh": "答案",
                "input_zh_sha256": _digest("\n一道题\n"),
                "reference_zh_sha256": _digest("答案"),
                "translation_version": "evalhub-zh-v1",
            }
        ],
    }
    path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    discovered = [
        {
            "id": "hexagon_gsm8k_01",
            "benchmark_id": "hexagon-gsm8k",
            "capability": "mathematics",
            "source_key": "test.jsonl:1",
            "selection_stratum": "test",
            "input": "English input",
            "reference": "1",
            "option_order": None,
        }
    ]

    merged = merge_translations(discovered, path)

    assert merged[0]["input_sha256"] == _digest("English input")
    assert merged[0]["reference_sha256"] == _digest("1")
    assert merged[0]["input_zh_sha256"] == _digest("一道题")
    assert merged[0]["reference_zh_sha256"] == _digest("答案")
    assert "input" not in merged[0]
    assert "reference" not in merged[0]
    assert merged[0]["input_zh"] == "一道题"


def test_manifest_builder_rejects_preserved_translation_with_stale_digest(
    tmp_path: Path,
) -> None:
    """既有翻译正文与保存摘要不一致时，构建器必须拒绝传播失去溯源的展示文本。

    Args:
        tmp_path: pytest 提供的隔离目录，用于构造被篡改的既有清单。
    """
    path = tmp_path / "manifest.json"
    existing = {
        "version": "1.0.0",
        "samples": [
            {
                "benchmark_id": "hexagon-gsm8k",
                "source_key": "test.jsonl:1",
                "input_zh": "已被修改",
                "reference_zh": None,
                "input_zh_sha256": _digest("旧文本"),
                "reference_zh_sha256": None,
                "translation_version": "evalhub-zh-v1",
            }
        ],
    }
    path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    discovered = [
        {
            "id": "hexagon_gsm8k_01",
            "benchmark_id": "hexagon-gsm8k",
            "capability": "mathematics",
            "source_key": "test.jsonl:1",
            "selection_stratum": "test",
            "input": "English input",
            "reference": "1",
            "option_order": None,
        }
    ]

    with pytest.raises(ValueError, match="preserved input_zh SHA-256 mismatch"):
        merge_translations(discovered, path)


def test_manifest_writer_is_byte_stable(tmp_path: Path) -> None:
    """相同样本数据重复写入必须产生完全相同的排序 UTF-8 JSON 字节。"""
    path = tmp_path / "manifest.json"
    samples = [{"source_key": "one", "benchmark_id": "hexagon-gsm8k"}]

    write_manifest(path, samples)
    first = path.read_bytes()
    write_manifest(path, samples)

    assert path.read_bytes() == first


def test_manifest_builder_runs_directly_from_repository_root() -> None:
    """仓库脚本必须自行发现 ``src`` 布局，不能依赖虚拟环境当前指向的可编辑安装。"""
    completed = subprocess.run(
        [sys.executable, "scripts/build_hexagon_manifest.py", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--output" in completed.stdout
