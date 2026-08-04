"""验证 Hexagon 固定选择清单的来源与双语溯源契约。"""

import json
from pathlib import Path

import pytest

from evalhub.datasets.hexagon_manifest import load_hexagon_manifest


def test_hexagon_manifest_requires_complete_bilingual_provenance(tmp_path: Path) -> None:
    """缺少固定样本必填溯源字段时，清单读取必须明确拒绝。"""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"version": "1.0.0", "samples": [{"id": "broken"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest sample is missing"):
        load_hexagon_manifest(path)
