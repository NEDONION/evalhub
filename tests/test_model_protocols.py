"""验证当前控制台模型都有明确且可组合的 Ollama 生成协议。"""

import pytest

from evalhub.model_protocols import (
    MODEL_GENERATION_PROTOCOL_VERSION,
    effective_generation_config,
    get_model_generation_profile,
    model_generation_profiles,
)


def test_all_current_model_options_have_registered_generation_profiles() -> None:
    """十二个推荐模型和 Granite 3.3 本机模型必须全部进入静态协议矩阵。"""
    # 精确集合防止新增控制台模型时忘记声明其 Benchmark 传输行为。
    expected = {
        "granite4.1:3b",
        "granite3.3:8b",
        "qwen3:4b",
        "qwen3:8b",
        "qwen3:14b",
        "ministral-3:8b",
        "gemma4:12b",
        "lfm2.5:8b",
        "north-mini-code-1.0:q4_K_M",
        "qwen2.5:0.5b",
        "qwen2.5:1.5b",
        "deepseek-r1:1.5b",
        "qwen2.5-coder:7b",
    }
    profiles = model_generation_profiles()

    assert set(profiles) == expected
    assert {profile.protocol_version for profile in profiles.values()} == {
        MODEL_GENERATION_PROTOCOL_VERSION
    }


@pytest.mark.parametrize(
    "model",
    [
        "qwen3:4b",
        "qwen3:8b",
        "qwen3:14b",
        "gemma4:12b",
        "lfm2.5:8b",
        "north-mini-code-1.0:q4_K_M",
        "deepseek-r1:1.5b",
    ],
)
def test_thinking_models_disable_thinking_for_answer_protocols(model: str) -> None:
    """会消耗独立思考预算的模型必须在正式 Benchmark 中发送顶层 think=false。"""
    config = effective_generation_config(model, {"temperature": 0, "num_predict": 256})

    assert config == {"temperature": 0, "num_predict": 256, "think": False}


def test_non_thinking_model_does_not_receive_unsupported_think_option() -> None:
    """不声明思考能力的模型不得收到可能不支持的 think 字段。"""
    config = effective_generation_config(
        "qwen2.5:1.5b", {"temperature": 0, "num_predict": 256}
    )

    assert config == {"temperature": 0, "num_predict": 256}


def test_unknown_model_has_stable_registration_error() -> None:
    """未知模型必须在工作流创建阶段提供稳定错误代码而非静默猜测。"""
    with pytest.raises(ValueError, match="model_protocol_not_registered: custom:latest"):
        get_model_generation_profile("custom:latest")
