"""验证 Hexagon 固定来源的摘要校验、缓存复用与目录注册。"""

import hashlib
from pathlib import Path

import pytest

from evalhub.datasets import dataset_catalog, prepare_dataset
from evalhub.datasets.hexagon_sources import (
    PinnedSource,
    _install_pinned_file,
    hexagon_source_specs,
    prepare_hexagon_dataset,
)
from evalhub.evaluators import IFEvalStrictEvaluator, default_evaluator_registry


def test_pinned_download_rejects_wrong_digest_without_replacing_cache(tmp_path: Path) -> None:
    """候选文件摘要错误时，已有缓存必须保持不变。"""
    destination = tmp_path / "source.jsonl"
    destination.write_bytes(b"known-good")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _install_pinned_file(
            destination,
            expected_sha256=hashlib.sha256(b"expected").hexdigest(),
            download=lambda candidate: candidate.write_bytes(b"corrupt"),
        )

    assert destination.read_bytes() == b"known-good"


def test_hexagon_gsm8k_reuses_official_cache_after_checksum_validation(tmp_path: Path) -> None:
    """Hexagon GSM8K 必须校验既有官方缓存，错误摘要不能被静默接受。"""
    path = tmp_path / "data/raw/gsm8k/test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare_hexagon_dataset("hexagon-gsm8k", root=tmp_path)


def test_hexagon_source_specs_expose_all_pinned_assets() -> None:
    """固定来源目录必须完整暴露七个 Hexagon 资产及其不可变描述。"""
    sources = hexagon_source_specs()

    assert set(sources) == {
        "hexagon-mmlu",
        "hexagon-ifeval",
        "hexagon-gsm8k",
        "hexagon-bbh",
        "hexagon-humaneval",
        "hexagon-truthfulqa",
        "hexagon-bbq",
    }
    assert all(isinstance(source, PinnedSource) for source in sources.values())
    assert sources["hexagon-gsm8k"].cache_path == "data/raw/gsm8k/test.jsonl"
    assert sources["hexagon-gsm8k"].url.endswith(
        "3101c7d5072418e28b9008a6636bde82a006892c/grade_school_math/data/test.jsonl"
    )


def test_dataset_catalog_uses_pinned_hexagon_paths() -> None:
    """数据集目录必须将 Hexagon 入口暴露为固定来源对应的本地路径。"""
    catalog = dataset_catalog()

    assert catalog["hexagon-gsm8k"].local_path == "data/raw/gsm8k/test.jsonl"
    assert catalog["hexagon-ifeval"].display_name == "IFEval"
    assert catalog["hexagon-humaneval"].evaluator_type == "pass@1"


def test_hexagon_ifeval_catalog_type_dispatches_to_the_registered_evaluator() -> None:
    """目录中 IFEval 的评测器类型必须可由默认注册表创建严格评分器。"""
    evaluator_type = dataset_catalog()["hexagon-ifeval"].evaluator_type

    assert isinstance(default_evaluator_registry().create(evaluator_type), IFEvalStrictEvaluator)


def test_prepare_dataset_routes_hexagon_ids_to_pinned_preparation(tmp_path: Path) -> None:
    """通用准备入口必须把 Hexagon ID 分派给固定来源准备流程。"""
    path = tmp_path / "data/raw/gsm8k/test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare_dataset("hexagon-gsm8k", root=tmp_path)
