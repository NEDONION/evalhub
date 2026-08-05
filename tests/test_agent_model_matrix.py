"""验证固定 10 模型 Agent 报告矩阵不会漂移或误触发重复运行。"""

import json
from pathlib import Path

import pytest

import scripts.run_agent_model_matrix as matrix_module


def test_agent_model_matrix_has_four_api_and_six_local_models() -> None:
    """主榜应固定 4 个 API、6 个本地模型且只保留两个 Qwen。"""
    api_models = [model for model in matrix_module.MODELS if model.provider_id is not None]
    local_models = [model for model in matrix_module.MODELS if model.provider_id is None]

    assert len(matrix_module.MODELS) == 10
    assert len(api_models) == 4
    assert len(local_models) == 6
    assert [model.model for model in local_models if model.model.startswith("qwen")] == [
        "qwen3:14b",
        "qwen3:4b",
    ]


def test_agent_model_matrix_reuses_existing_v3_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一模型已有 v3 结果时应直接读取，避免重复消耗 API 和本地算力。"""
    monkeypatch.setattr(matrix_module, "OUTPUT_ROOT", tmp_path)
    configuration = matrix_module.MODELS[0]
    expected = {"benchmark_version": "coding-mini-v3", "passed_samples": 4}
    (tmp_path / f"{configuration.slug}.json").write_text(
        json.dumps(expected),
        encoding="utf-8",
    )

    assert matrix_module.run_model(configuration) == expected
