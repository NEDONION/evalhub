"""验证公开 Benchmark 强制更新只在新资产有效时替换旧缓存。"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from evalhub.datasets import prepare_dataset


def gsm8k_row(question: str, answer: str) -> str:
    """构造一条符合官方最终答案标记的 JSONL 记录。"""
    return json.dumps({"question": question, "answer": f"work\n#### {answer}"}) + "\n"


def write_mmlu_archive(path: Path, *, csv_content: str | None = None) -> None:
    """创建最小 MMLU 归档；空内容用于验证缺少测试集的失败分支。"""
    with tarfile.open(path, "w") as archive:
        if csv_content is None:
            payload = b"fixture"
            info = tarfile.TarInfo("data/README.txt")
        else:
            payload = csv_content.encode()
            info = tarfile.TarInfo("data/test/abstract_algebra_test.csv")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_non_force_prepare_keeps_existing_gsm8k_cache(tmp_path: Path) -> None:
    """默认准备流程必须继续复用已有缓存且不触发网络。"""
    target = tmp_path / "data/raw/gsm8k/test.jsonl"
    target.parent.mkdir(parents=True)
    original = gsm8k_row("old question", "1")
    target.write_text(original, encoding="utf-8")

    with patch("evalhub.datasets.loaders.urlretrieve") as retrieve:
        result = prepare_dataset("gsm8k", root=tmp_path)

    assert result == target
    assert target.read_text(encoding="utf-8") == original
    retrieve.assert_not_called()


def test_prepare_dataset_routes_hexagon_gsm8k_to_checksum_validation(tmp_path: Path) -> None:
    """Hexagon 别名必须优先走固定摘要校验，不能沿用旧 GSM8K 缓存语义。"""
    target = tmp_path / "data/raw/gsm8k/test.jsonl"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare_dataset("hexagon-gsm8k", root=tmp_path)


def test_force_refresh_replaces_gsm8k_only_after_validation(tmp_path: Path) -> None:
    """有效的新 GSM8K 文件必须原子替换已有缓存。"""
    target = tmp_path / "data/raw/gsm8k/test.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text(gsm8k_row("old question", "1"), encoding="utf-8")

    def download(_url: str, destination: str | Path):
        Path(destination).write_text(gsm8k_row("new question", "2"), encoding="utf-8")
        return str(destination), None

    with patch("evalhub.datasets.loaders.urlretrieve", side_effect=download):
        result = prepare_dataset("gsm8k", root=tmp_path, force=True)

    assert result == target
    assert target.read_text(encoding="utf-8") == gsm8k_row("new question", "2")
    assert list(target.parent.glob(".evalhub-*")) == []


def test_invalid_gsm8k_refresh_preserves_old_cache(tmp_path: Path) -> None:
    """坏 JSONL 不能覆盖仍可使用的 GSM8K 文件。"""
    target = tmp_path / "data/raw/gsm8k/test.jsonl"
    target.parent.mkdir(parents=True)
    original = gsm8k_row("old question", "1")
    target.write_text(original, encoding="utf-8")

    def download(_url: str, destination: str | Path):
        Path(destination).write_text('{"question":"broken"}\n', encoding="utf-8")
        return str(destination), None

    with (
        patch("evalhub.datasets.loaders.urlretrieve", side_effect=download),
        pytest.raises(ValueError, match="GSM8K"),
    ):
        prepare_dataset("gsm8k", root=tmp_path, force=True)

    assert target.read_text(encoding="utf-8") == original
    assert list(target.parent.glob(".evalhub-*")) == []


def test_force_refresh_replaces_valid_mmlu_directory_and_archive(tmp_path: Path) -> None:
    """验证完成的 MMLU 候选目录和归档必须一起替换旧版本。"""
    mmlu_root = tmp_path / "data/raw/mmlu"
    target = mmlu_root / "data/test/abstract_algebra_test.csv"
    target.parent.mkdir(parents=True)
    target.write_text("old,a,b,c,d,A\n", encoding="utf-8")
    archive_path = mmlu_root / "data.tar"
    archive_path.write_bytes(b"old archive")

    def download(_url: str, destination: str | Path):
        write_mmlu_archive(Path(destination), csv_content="new,a,b,c,d,B\n")
        return str(destination), None

    with patch("evalhub.datasets.loaders.urlretrieve", side_effect=download):
        result = prepare_dataset("mmlu", root=tmp_path, force=True)

    assert result == mmlu_root / "data/test"
    assert target.read_text(encoding="utf-8") == "new,a,b,c,d,B\n"
    with tarfile.open(archive_path) as archive:
        assert "data/test/abstract_algebra_test.csv" in archive.getnames()
    assert list(mmlu_root.glob(".evalhub-*")) == []


def test_incomplete_mmlu_refresh_preserves_old_directory_and_archive(tmp_path: Path) -> None:
    """不含测试 CSV 的新归档必须被拒绝且不改变旧资产。"""
    mmlu_root = tmp_path / "data/raw/mmlu"
    target = mmlu_root / "data/test/abstract_algebra_test.csv"
    target.parent.mkdir(parents=True)
    target.write_text("old,a,b,c,d,A\n", encoding="utf-8")
    archive_path = mmlu_root / "data.tar"
    archive_path.write_bytes(b"old archive")

    def download(_url: str, destination: str | Path):
        write_mmlu_archive(Path(destination))
        return str(destination), None

    with (
        patch("evalhub.datasets.loaders.urlretrieve", side_effect=download),
        pytest.raises(ValueError, match="MMLU"),
    ):
        prepare_dataset("mmlu", root=tmp_path, force=True)

    assert target.read_text(encoding="utf-8") == "old,a,b,c,d,A\n"
    assert archive_path.read_bytes() == b"old archive"
    assert list(mmlu_root.glob(".evalhub-*")) == []


def test_unsafe_mmlu_refresh_preserves_old_cache(tmp_path: Path) -> None:
    """包含越界成员的归档必须在写入数据目录前被拒绝。"""
    mmlu_root = tmp_path / "data/raw/mmlu"
    target = mmlu_root / "data/test/abstract_algebra_test.csv"
    target.parent.mkdir(parents=True)
    target.write_text("old,a,b,c,d,A\n", encoding="utf-8")

    def download(_url: str, destination: str | Path):
        with tarfile.open(destination, "w") as archive:
            payload = b"escape"
            info = tarfile.TarInfo("../escape.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return str(destination), None

    with (
        patch("evalhub.datasets.loaders.urlretrieve", side_effect=download),
        pytest.raises(RuntimeError, match="unsafe archive"),
    ):
        prepare_dataset("mmlu", root=tmp_path, force=True)

    assert target.read_text(encoding="utf-8") == "old,a,b,c,d,A\n"
    assert not (tmp_path / "data/raw/escape.txt").exists()
    assert list(mmlu_root.glob(".evalhub-*")) == []


def test_mmlu_refresh_rejects_common_prefix_path_escape(tmp_path: Path) -> None:
    """与目标目录同前缀的兄弟目录也不能绕过归档边界检查。"""
    mmlu_root = tmp_path / "data/raw/mmlu"
    target = mmlu_root / "data/test/abstract_algebra_test.csv"
    target.parent.mkdir(parents=True)
    target.write_text("old,a,b,c,d,A\n", encoding="utf-8")

    def download(_url: str, destination: str | Path):
        with tarfile.open(destination, "w") as archive:
            payload = b"escape"
            info = tarfile.TarInfo("../extracted_evil/escape.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return str(destination), None

    with (
        patch("evalhub.datasets.loaders.urlretrieve", side_effect=download),
        pytest.raises(RuntimeError, match="unsafe archive"),
    ):
        prepare_dataset("mmlu", root=tmp_path, force=True)

    assert target.read_text(encoding="utf-8") == "old,a,b,c,d,A\n"
    assert list(mmlu_root.glob(".evalhub-*")) == []
