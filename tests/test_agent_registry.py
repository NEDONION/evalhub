"""验证完整 Agent Registry 的稳定目录与拒绝边界。"""

import pytest

import evalhub.agent as agent_module


def test_agent_registry_exposes_supported_agents_in_stable_order() -> None:
    """目录必须同时暴露 Pi 与 MiniClaw，并声明各自的模型管理责任。"""
    definitions = getattr(agent_module, "agent_definitions", lambda: ())()

    assert [item.id for item in definitions] == ["pi", "miniclaw"]
    assert [item.model_mode for item in definitions] == ["evalhub", "agent"]


def test_agent_registry_rejects_unknown_agent() -> None:
    """未知 Agent 不能静默回退到 Pi，否则任务会评测错误的完整产品。"""
    create_agent_runner = getattr(agent_module, "create_agent_runner", lambda _: None)
    with pytest.raises(ValueError, match="unknown agent: missing"):
        create_agent_runner("missing")
