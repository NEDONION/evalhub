"""验证本地 HTTP 请求处理器暴露的异步评测任务契约。"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from typing import cast
from unittest.mock import patch

from evalhub.agent.base import AgentStatus
from evalhub.benchmarks import Capability, ExecutorReadiness
from evalhub.credentials import CredentialCipher
from evalhub.model_providers import ModelProviderRepository
from evalhub.server import EvalHubRequestHandler, _dataset_is_prepared
from evalhub.tasks import (
    EvaluationNode,
    EvaluationNodeEvent,
    EvaluationSampleCheckpoint,
    EvaluationSamplePage,
    EvaluationTask,
    EvaluationType,
    TaskConflictError,
    TaskNotFoundError,
    TaskRequest,
)
from evalhub.tasks.performance import ModelPerformanceReport, build_model_performance
from evalhub.tasks.presentation import sample_checkpoint, task_detail


def request_fixture() -> TaskRequest:
    """构造任务 API 创建端点应转换出的完整请求。"""
    return TaskRequest(
        dataset="gsm8k",
        adapter="oracle",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        sample_mode="quick",
        subject="all",
        limit=None,
    )


def task_fixture(*, status: str = "pending", with_result: bool = False) -> EvaluationTask:
    """构造列表与详情序列化使用的稳定任务快照。"""
    created = datetime(2026, 8, 4, 2, 0, tzinfo=UTC)
    result = None
    if with_result:
        result = {
            "job_id": "job_api",
            "status": "success",
            "dataset": "gsm8k",
            "benchmark": "GSM8K 测试集",
            "model": "local-test",
            "adapter": "oracle",
            "metric": "numeric_exact_match",
            "total_samples": 5,
            "passed_samples": 4,
            "average_score": 0.8,
            "failed_sample_ids": ["sample_5"],
            "failed_examples": [],
        }
    # 固定时间与资源读数让响应字段可按字面量验证，不依赖墙上时钟。
    return EvaluationTask(
        id="job_api",
        request=request_fixture(),
        status=cast(object, status),
        completed_samples=5 if with_result else 0,
        total_samples=5 if with_result else 0,
        created_at=created,
        updated_at=created,
        started_at=created if status != "pending" else None,
        finished_at=created if with_result else None,
        cpu_percent=12.5,
        peak_cpu_percent=40.0,
        memory_bytes=1024,
        peak_memory_bytes=2048,
        gpu_supported=False,
        benchmark="GSM8K 测试集" if with_result else None,
        passed_samples=4 if with_result else None,
        average_score=0.8 if with_result else None,
        result=result,
    )


def node_fixture(*, status: str = "failed") -> EvaluationNode:
    """构造节点详情、审计和重试端点使用的稳定节点快照。"""
    created = datetime(2026, 8, 4, 2, 0, tzinfo=UTC)
    return EvaluationNode(
        id="node_api",
        task_id="job_api",
        node_key="benchmark:gsm8k",
        kind="benchmark",
        depends_on=("prepare_assets",),
        status=cast(object, status),
        attempt_count=2,
        max_attempts=3,
        input={"benchmark_id": "gsm8k"},
        checkpoint={"completed_samples": 4, "total_samples": 5},
        output=None,
        error_type="connection_error" if status == "failed" else None,
        error_message="Ollama connection failed" if status == "failed" else None,
        completed_samples=4,
        total_samples=5,
        created_at=created,
        updated_at=created,
        started_at=created,
        attempt_started_at=created,
        finished_at=created if status == "failed" else None,
        elapsed_ms=1250,
    )


class FakeTaskService:
    """提供请求处理器测试所需的任务服务可观察行为。"""

    def __init__(self, task: EvaluationTask) -> None:
        """保存唯一任务并记录收到的创建请求。"""
        self.task = task
        self.node = node_fixture()
        self.submitted_request: TaskRequest | None = None
        self.performance_scope: str | None = None
        self.performance_evaluation_type: EvaluationType = "model"

    def submit(self, request: TaskRequest) -> EvaluationTask:
        """记录转换后的请求并返回排队任务。"""
        self.submitted_request = request
        return self.task

    def list(self) -> list[EvaluationTask]:
        """返回包含唯一任务的轻量列表。"""
        return [self.task]

    def model_performance(
        self,
        scope: str | None = None,
        evaluation_type: EvaluationType = "model",
    ) -> ModelPerformanceReport:
        """记录成绩范围并使用真实聚合逻辑返回可序列化报告。"""
        self.performance_scope = scope
        self.performance_evaluation_type = evaluation_type
        tasks = [self.task] if self.task.average_score is not None else []
        return build_model_performance(tasks, scope, evaluation_type=evaluation_type)

    def get(self, task_id: str) -> EvaluationTask:
        """读取匹配任务，未知 ID 转换为仓储级缺失异常。"""
        if task_id != self.task.id:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return self.task

    def list_nodes(self, task_id: str) -> list[EvaluationNode]:
        """返回当前任务的唯一工作流节点。"""
        self.get(task_id)
        return [self.node]

    def get_node(self, task_id: str, node_id: str) -> EvaluationNode:
        """按任务和节点复合边界读取节点。"""
        self.get(task_id)
        if node_id != self.node.id:
            raise TaskNotFoundError(f"node not found: {node_id}")
        return self.node

    def list_node_events(self, task_id: str, node_id: str) -> list[EvaluationNodeEvent]:
        """返回节点开始和失败事件中的一个稳定审计记录。"""
        self.get_node(task_id, node_id)
        return [
            EvaluationNodeEvent(
                id=1,
                task_id=task_id,
                node_id=node_id,
                event_type="node_failed",
                from_status="running",
                to_status="failed",
                attempt=2,
                actor="worker",
                message="Ollama connection failed",
                payload={"error_type": "connection_error"},
                created_at=datetime(2026, 8, 4, 2, 0, tzinfo=UTC),
            )
        ]

    def list_node_samples(
        self,
        task_id: str,
        node_id: str,
        *,
        limit: int,
        cursor: str | None,
        status: str | None,
    ) -> EvaluationSamplePage:
        """返回一页失败样本并保留调用方分页过滤语义。"""
        self.get_node(task_id, node_id)
        assert limit == 20
        assert cursor == "3:sample_4"
        assert status == "failed"
        sample = EvaluationSampleCheckpoint(
            node_id=node_id,
            task_id=task_id,
            sample_key="sample_5",
            sample_index=4,
            status="failed",
            attempt_count=2,
            input={"input": "2 + 2", "reference": "4"},
            last_error={"message": "timeout"},
            created_at=datetime(2026, 8, 4, 2, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 4, 2, 1, tzinfo=UTC),
        )
        return EvaluationSamplePage(items=(sample,), next_cursor="4:sample_5")

    def retry_node(self, task_id: str, node_id: str) -> EvaluationNode:
        """记录合法节点重试并返回回到待执行状态的节点。"""
        self.get_node(task_id, node_id)
        self.node = node_fixture(status="pending")
        return self.node

    def cancel(self, task_id: str) -> EvaluationTask:
        """模拟排队任务取消或终态任务冲突。"""
        if self.task.status in {"success", "failed", "canceled"}:
            raise TaskConflictError(f"task is already {self.task.status}")
        return self.task


def call_handler(
    *,
    method: str,
    path: str,
    service: FakeTaskService,
    payload: object | None = None,
    provider_repository: ModelProviderRepository | None = None,
    client_host: str = "127.0.0.1",
) -> tuple[int, dict[str, object]]:
    """绕过真实套接字调用请求处理器并捕获 JSON 响应。

    Args:
        method: 需要执行的 ``GET``、``POST``、``PUT`` 或 ``DELETE`` 方法。
        path: 包含完整 API 路径的请求目标。
        service: 注入处理器的可控任务服务。
        payload: POST 请求解析后应获得的 JSON 值。
        provider_repository: 可选的临时模型服务商仓储。
        client_host: 用于验证凭据写操作回环限制的客户端地址。

    Returns:
        HTTP 状态码与处理器发送的 JSON 正文。
    """
    handler = cast(EvalHubRequestHandler, object.__new__(EvalHubRequestHandler))
    handler.path = path
    handler.task_service = cast(object, service)
    handler.provider_repository = provider_repository
    handler.client_address = (client_host, 12345)
    captured: list[tuple[int, dict[str, object]]] = []

    def capture_json(
        self: EvalHubRequestHandler,
        response: dict[str, object],
        status: int = 200,
    ) -> None:
        """记录处理器将要发送给客户端的状态码与正文。"""
        captured.append((status, response))

    # 实例级替换只隔离套接字读写，路由、参数转换和异常映射仍使用生产代码。
    handler._json = MethodType(capture_json, handler)
    handler._read_json = MethodType(lambda self: payload or {}, handler)
    getattr(handler, f"do_{method}")()
    assert len(captured) == 1
    return captured[0]


def test_create_evaluation_returns_accepted_task() -> None:
    """创建端点应把前端正文转换为任务请求并立即返回 202。"""
    service = FakeTaskService(task_fixture())
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        payload={
            "dataset": "gsm8k",
            "adapter": "oracle",
            "model": "local-test",
            "base_url": "http://127.0.0.1:11434",
            "sample_mode": "quick",
        },
    )

    assert status == 202
    assert response["ok"] is True
    assert response["task"]["status"] == "pending"
    assert service.submitted_request == request_fixture()


def test_create_agent_evaluation_returns_accepted_task() -> None:
    """合法 Pi Agent 请求应保留评测类型和框架后立即进入队列。"""
    service = FakeTaskService(task_fixture())
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        payload={
            "evaluation_type": "agent",
            "agent_framework": "pi",
            "dataset": "coding_mini",
            "adapter": "ollama",
            "model": "qwen2.5-coder:7b",
            "base_url": "http://127.0.0.1:11434",
            "sample_mode": "all",
            "agent_difficulty": "hard",
        },
    )

    # 任务 API 只负责校验和入队，不能在创建请求内同步运行耗时 Agent。
    assert status == 202
    assert response["ok"] is True
    assert service.submitted_request is not None
    assert service.submitted_request.evaluation_type == "agent"
    assert service.submitted_request.agent_framework == "pi"
    assert service.submitted_request.agent_difficulty == "hard"
    assert service.submitted_request.sample_mode == "all"
    assert service.submitted_request.dataset == "coding_mini"


def test_create_miniclaw_agent_evaluation_uses_agent_managed_runtime() -> None:
    """MiniClaw 请求不应伪造模型字段，服务端只保存稳定内部身份。"""
    service = FakeTaskService(task_fixture())
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        payload={
            "evaluation_type": "agent",
            "agent_framework": "miniclaw",
            "dataset": "coding_mini",
            "sample_mode": "all",
            "agent_difficulty": "all",
        },
    )

    assert status == 202
    assert response["ok"] is True
    assert service.submitted_request is not None
    assert service.submitted_request.adapter == "agent-managed"
    assert service.submitted_request.model == "miniclaw"
    assert service.submitted_request.base_url == ""
    assert service.submitted_request.agent_framework == "miniclaw"


def test_create_miniclaw_agent_evaluation_rejects_evalhub_model_fields() -> None:
    """浏览器不得用 EvalHub 模型字段覆盖 MiniClaw 自己的运行时配置。"""
    base_payload = {
        "evaluation_type": "agent",
        "agent_framework": "miniclaw",
        "dataset": "coding_mini",
        "sample_mode": "all",
    }
    invalid_fields = {
        "adapter": "ollama",
        "model": "qwen",
        "base_url": "http://127.0.0.1:11434",
        "provider_id": "deepseek",
    }

    # 每个模型字段单独提交，确保错误不会被另一个字段的校验顺序掩盖。
    for field, value in invalid_fields.items():
        service = FakeTaskService(task_fixture())
        status, response = call_handler(
            method="POST",
            path="/api/evaluations",
            service=service,
            payload={**base_payload, field: value},
        )
        assert status == 400
        assert response["error"] == f"{field} is managed by miniclaw"
        assert service.submitted_request is None


def test_agent_catalog_exposes_complete_agent_readiness() -> None:
    """Agent 目录应返回模型归属和本机就绪状态，供表单隐藏无效模型字段。"""
    miniclaw = AgentStatus(
        id="miniclaw",
        name="MiniClaw",
        description="使用自身运行时的完整 Agent",
        model_mode="agent",
        available=True,
        version="0.1.0",
        model="deepseek-v4-pro",
        message="ready",
    )
    with patch("evalhub.server.agent_statuses", return_value=(miniclaw,)):
        status, response = call_handler(
            method="GET",
            path="/api/agents",
            service=FakeTaskService(task_fixture()),
        )

    assert status == 200
    assert response == {
        "agents": [
            {
                "id": "miniclaw",
                "name": "MiniClaw",
                "description": "使用自身运行时的完整 Agent",
                "model_mode": "agent",
                "available": True,
                "version": "0.1.0",
                "model": "deepseek-v4-pro",
                "message": "ready",
            }
        ]
    }


def test_create_deepseek_agent_evaluation_returns_accepted_task(tmp_path: Path) -> None:
    """已配置凭据的官方 DeepSeek Provider 应可创建 Pi Agent 评测任务。"""
    repository = _provider_repository(tmp_path)
    repository.save(
        "deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key="sk-deepseek-test",
    )
    service = FakeTaskService(task_fixture())

    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        provider_repository=repository,
        payload={
            "evaluation_type": "agent",
            "agent_framework": "pi",
            "dataset": "coding_mini",
            "adapter": "openai-compatible",
            "provider_id": "deepseek",
            "model": "deepseek-v4-pro",
            "sample_mode": "all",
            "agent_difficulty": "easy",
        },
    )

    assert status == 202
    assert response["ok"] is True
    assert service.submitted_request is not None
    assert service.submitted_request.adapter == "openai-compatible"
    assert service.submitted_request.provider_id == "deepseek"
    assert service.submitted_request.base_url == "https://api.deepseek.com"


def test_create_siliconflow_agent_evaluation_returns_accepted_task(tmp_path: Path) -> None:
    """已配置凭据的官方 SiliconFlow Provider 应可创建 Pi Agent 评测任务。"""
    repository = _provider_repository(tmp_path)
    repository.save(
        "siliconflow",
        name="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        api_key="sk-siliconflow-test",
    )
    service = FakeTaskService(task_fixture())

    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        provider_repository=repository,
        payload={
            "evaluation_type": "agent",
            "agent_framework": "pi",
            "dataset": "coding_mini",
            "adapter": "openai-compatible",
            "provider_id": "siliconflow",
            "model": "moonshotai/Kimi-K2.7-Code",
            "sample_mode": "all",
            "agent_difficulty": "easy",
        },
    )

    assert status == 202
    assert response["ok"] is True
    assert service.submitted_request is not None
    assert service.submitted_request.provider_id == "siliconflow"
    assert service.submitted_request.base_url == "https://api.siliconflow.cn/v1"


def test_create_agent_evaluation_rejects_unsupported_combinations() -> None:
    """Agent 只允许已实现的 Pi、Coding Mini 与受支持 Provider 组合。"""
    base_payload = {
        "evaluation_type": "agent",
        "agent_framework": "pi",
        "dataset": "coding_mini",
        "adapter": "ollama",
        "model": "local-test",
        "sample_mode": "all",
    }
    invalid_cases = [
        (
            {**base_payload, "agent_framework": "unknown"},
            "agent_framework must be one of: pi, miniclaw",
        ),
        ({**base_payload, "dataset": "gsm8k"}, "agent dataset must be coding_mini"),
        (
            {**base_payload, "adapter": "oracle"},
            "agent adapter must be one of: ollama, openai-compatible",
        ),
        (
            {**base_payload, "agent_difficulty": "expert"},
            "agent_difficulty must be one of: all, easy, medium, hard",
        ),
        (
            {**base_payload, "agent_difficulty": ""},
            "agent_difficulty must be one of: all, easy, medium, hard",
        ),
        (
            {**base_payload, "agent_difficulty": None},
            "agent_difficulty must be one of: all, easy, medium, hard",
        ),
    ]

    # 每个非法组合都必须在写入 SQLite 前被拒绝，并返回可直接修正的字段错误。
    for payload, expected_error in invalid_cases:
        status, response = call_handler(
            method="POST",
            path="/api/evaluations",
            service=FakeTaskService(task_fixture()),
            payload=payload,
        )
        assert status == 400
        assert response == {"ok": False, "error": expected_error}


def test_create_model_evaluation_rejects_agent_difficulty() -> None:
    """模型评测携带 Agent 专属难度时应返回明确客户端错误。"""
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=FakeTaskService(task_fixture()),
        payload={
            "dataset": "gsm8k",
            "adapter": "oracle",
            "model": "local-test",
            "sample_mode": "all",
            "agent_difficulty": "easy",
        },
    )

    assert status == 400
    assert response == {
        "ok": False,
        "error": "agent_difficulty is only valid for agent evaluations",
    }


def test_list_evaluations_excludes_full_result() -> None:
    """列表响应应提供结果摘要和资源，但不得携带完整失败样例正文。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))
    status, response = call_handler(method="GET", path="/api/evaluations", service=service)

    task = response["tasks"][0]
    assert status == 200
    assert "result" not in task
    assert task["result_summary"] == {
        "benchmark": "GSM8K 测试集",
        "total_samples": 5,
        "passed_samples": 4,
        "average_score": 0.8,
    }
    assert task["resources"]["gpu"]["supported"] is False
    assert task["evaluation_type"] == "model"
    assert task["agent_framework"] is None


def test_get_model_performance_serializes_default_and_requested_scope() -> None:
    """成绩端点应传递范围并返回不含完整任务正文的排行榜。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))

    status, response = call_handler(
        method="GET",
        path="/api/model-performance?scope=benchmark%3Agsm8k",
        service=service,
    )

    assert status == 200
    assert service.performance_scope == "benchmark:gsm8k"
    assert response["selected_scope"]["key"] == "benchmark:gsm8k"
    assert response["models"][0]["model"] == "local-test"
    assert response["models"][0]["best_score"] == 0.8
    assert "result" not in response["models"][0]


def test_get_model_performance_selects_agent_scores_without_mixing_model_scores() -> None:
    """成绩端点应接受独立 Agent 口径，避免把两种评分协议放进同一排行榜。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))

    status, response = call_handler(
        method="GET",
        path="/api/model-performance?evaluation_type=agent",
        service=service,
    )

    assert status == 200
    assert service.performance_evaluation_type == "agent"
    assert response["models"] == []


def test_get_model_performance_rejects_unknown_evaluation_type() -> None:
    """未知成绩类型应在 HTTP 边界返回 400，不能静默回退到模型排行榜。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))

    status, response = call_handler(
        method="GET",
        path="/api/model-performance?evaluation_type=workflow",
        service=service,
    )

    assert status == 400
    assert response == {
        "ok": False,
        "error": "evaluation_type must be model or agent",
    }


def test_get_model_performance_rejects_unknown_scope() -> None:
    """未知成绩范围应返回结构化 400，而不是回退到不可比的默认榜单。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))

    status, response = call_handler(
        method="GET",
        path="/api/model-performance?scope=benchmark%3Amissing",
        service=service,
    )

    assert status == 400
    assert response == {
        "ok": False,
        "error": "unknown model performance scope: benchmark:missing",
    }


def test_list_evaluations_includes_suite_id() -> None:
    """套件任务列表项应携带稳定标识，供前端区分套件和单项 Benchmark。"""
    task = task_fixture()
    suite_request = replace(task.request, suite_id="llm-industry-core-v1")
    service = FakeTaskService(replace(task, request=suite_request))

    # 通过列表路由验证轻量摘要契约，不要求前端额外读取任务详情。
    status, response = call_handler(method="GET", path="/api/evaluations", service=service)

    assert status == 200
    assert response["tasks"][0]["suite_id"] == "llm-industry-core-v1"


def test_get_evaluation_detail_includes_full_result() -> None:
    """详情端点应只为选中任务返回完整评测结果。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))
    status, response = call_handler(
        method="GET",
        path="/api/evaluations/job_api",
        service=service,
    )

    assert status == 200
    assert response["task"]["result"]["average_score"] == 0.8
    assert response["task"]["request"]["sample_mode"] == "quick"
    assert response["task"]["nodes"][0]["node_key"] == "benchmark:gsm8k"
    assert response["task"]["nodes"][0]["timing"]["elapsed_ms"] == 1250


def test_get_evaluation_detail_uses_finalizer_output_when_result_is_missing() -> None:
    """任务结果尚未单独落盘时，详情应回退到已完成的汇总节点输出。"""
    service = FakeTaskService(task_fixture(status="success"))
    # 构造汇总节点已持久化、任务结果列仍为空的短暂一致性窗口。
    service.node = replace(
        service.node,
        kind="workflow_finalize",
        status="success",
        output={"status": "partial", "overall_score": 0.75},
    )

    # 通过真实路由验证 API 层最终响应，而不是绑定内部辅助函数的调用细节。
    status, response = call_handler(
        method="GET",
        path="/api/evaluations/job_api",
        service=service,
    )

    assert status == 200
    assert response["task"]["result"] == {"status": "partial", "overall_score": 0.75}


def test_get_node_detail_includes_checkpoint_and_audit_events() -> None:
    """节点详情应返回最新快照和不可变审计事件。"""
    service = FakeTaskService(task_fixture(status="failed"))
    status, response = call_handler(
        method="GET",
        path="/api/evaluations/job_api/nodes/node_api",
        service=service,
    )

    assert status == 200
    node = response["node"]
    assert node["checkpoint"] == {"completed_samples": 4, "total_samples": 5}
    assert node["events"][0]["event_type"] == "node_failed"
    assert node["events"][0]["attempt"] == 2


def test_failed_suite_detail_keeps_finalized_partial_capability_profile() -> None:
    """部分 Benchmark 阻塞时，详情仍应展示已经持久化的模型能力画像。"""
    partial_result = {
        "status": "partial",
        "capability_profile": {"status": "partial", "capabilities": {}},
    }
    finalizer = replace(
        node_fixture(status="success"),
        node_key="workflow_finalize",
        kind="workflow_finalize",
        output=partial_result,
    )

    detail = task_detail(task_fixture(status="failed"), nodes=[finalizer])

    assert detail["result"] == partial_result


def test_list_node_samples_preserves_cursor_and_failure_filter() -> None:
    """样本端点应把分页和失败过滤传给仓储并返回下页游标。"""
    service = FakeTaskService(task_fixture(status="failed"))
    status, response = call_handler(
        method="GET",
        path=(
            "/api/evaluations/job_api/nodes/node_api/samples"
            "?status=failed&limit=20&cursor=3%3Asample_4"
        ),
        service=service,
    )

    assert status == 200
    assert response["samples"][0]["sample_key"] == "sample_5"
    assert response["samples"][0]["last_error"] is None
    assert response["next_cursor"] == "4:sample_5"


def test_retry_failed_node_returns_pending_snapshot() -> None:
    """节点重试端点应返回 202 并让选中节点重新进入待执行状态。"""
    service = FakeTaskService(task_fixture(status="failed"))
    status, response = call_handler(
        method="POST",
        path="/api/evaluations/job_api/nodes/node_api/retry",
        service=service,
    )

    assert status == 202
    assert response["ok"] is True
    assert response["node"]["status"] == "pending"


def test_registry_endpoints_expose_real_readiness_without_false_availability() -> None:
    """全部依赖就绪时 Registry 与 Suite 应共同报告 13 项本地可运行。"""
    service = FakeTaskService(task_fixture())
    with patch(
        "evalhub.server.benchmark_readiness",
        return_value=ExecutorReadiness(True, "ready", "fixture ready"),
    ):
        benchmark_status, benchmark_response = call_handler(
            method="GET",
            path="/api/benchmarks",
            service=service,
        )
        suite_status, suite_response = call_handler(
            method="GET",
            path="/api/suites",
            service=service,
        )

    benchmarks = {item["id"]: item for item in benchmark_response["benchmarks"]}
    assert benchmark_status == suite_status == 200
    assert len(benchmarks) == 20
    assert benchmarks["gsm8k"]["locally_runnable"] is True
    assert benchmarks["mmlu-pro"]["locally_runnable"] is True
    assert benchmarks["humaneval"]["locally_runnable"] is True
    assert suite_response["suites"][0]["benchmark_count"] == 13
    assert suite_response["suites"][0]["locally_runnable_count"] == 13


def test_registry_endpoints_preserve_mixed_executor_readiness(monkeypatch) -> None:
    """Registry 应按共享检查结果区分当前已接通与待配置执行器。"""
    monkeypatch.setattr(
        "evalhub.server.benchmark_readiness",
        lambda spec: ExecutorReadiness(
            ready=spec.executor.value == "native",
            code="ready" if spec.executor.value == "native" else "executor_not_ready",
            message=(
                "lm_eval 执行器尚未配置"
                if spec.executor.value == "lm_eval"
                else "fixture readiness"
            ),
        ),
    )

    status, response = call_handler(
        method="GET", path="/api/benchmarks", service=FakeTaskService(task_fixture())
    )
    benchmarks = {item["id"]: item for item in response["benchmarks"]}

    assert status == 200
    assert benchmarks["gsm8k"]["locally_runnable"] is True
    assert benchmarks["mmlu-pro"]["locally_runnable"] is False
    assert benchmarks["mmlu-pro"]["readiness_reason"] == "lm_eval 执行器尚未配置"


def test_dataset_endpoint_lists_all_registry_assets() -> None:
    """资产页数据源必须同时包含行业套件与专业 Hexagon 的注册项。"""
    service = FakeTaskService(task_fixture())
    with (
        patch(
            "evalhub.server.benchmark_readiness",
            return_value=ExecutorReadiness(True, "ready", "fixture ready"),
        ),
        patch(
            "evalhub.server._dataset_is_prepared",
            side_effect=lambda dataset: dataset == "ifeval",
        ),
    ):
        status, response = call_handler(
            method="GET",
            path="/api/datasets",
            service=service,
        )

    datasets = {item["name"]: item for item in response["datasets"]}
    assert status == 200
    assert len(datasets) == 20
    assert datasets["gsm8k"]["executor"] == "native"
    assert datasets["mmlu-pro"]["executor"] == "lm_eval"
    assert datasets["humaneval"]["executor"] == "sandboxed_code"
    assert datasets["bbq"]["capability_label"] == "安全可信"
    assert datasets["ifeval"]["prepared"] is True
    assert datasets["mmlu-pro"]["prepared"] is False


def test_external_dataset_requires_verified_preparation_marker(
    tmp_path, monkeypatch
) -> None:
    """旧版存在但未真实加载数据的标记不能显示为已缓存。"""
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / ".runtime/benchmarks/ifeval.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"benchmark_id": "ifeval"}', encoding="utf-8")

    assert _dataset_is_prepared("ifeval") is False

    marker.write_text(
        '{"benchmark_id": "ifeval", "preparation": "task_data_loaded"}',
        encoding="utf-8",
    )
    assert _dataset_is_prepared("ifeval") is True


def test_hexagon_suite_api_reports_thirty_samples_and_member_readiness(monkeypatch) -> None:
    """Hexagon 套件应公开固定样本数、六维能力和每个成员的真实就绪状态。"""
    monkeypatch.setattr(
        "evalhub.server.benchmark_readiness",
        lambda spec: ExecutorReadiness(
            ready=spec.id != "hexagon-humaneval" and spec.executor.value == "native",
            code=(
                "ready"
                if spec.id != "hexagon-humaneval" and spec.executor.value == "native"
                else "executor_not_ready"
            ),
            message="fixture readiness",
        ),
    )
    status, response = call_handler(
        method="GET", path="/api/suites", service=FakeTaskService(task_fixture())
    )

    suite = next(item for item in response["suites"] if item["id"] == "evalhub-hexagon-v1")
    humaneval = next(item for item in suite["members"] if item["id"] == "hexagon-humaneval")

    assert status == 200
    assert suite["expected_sample_count"] == 30
    assert suite["benchmark_count"] == 7
    assert suite["capabilities"] == [item.value for item in Capability]
    assert suite["ready_count"] == sum(item["readiness"]["ready"] for item in suite["members"])
    assert humaneval["readiness"]["code"] == "executor_not_ready"
    assert humaneval["readiness"]["build_command"] == "./scripts/build_humaneval_image.sh"


def test_sample_checkpoint_exposes_only_safe_translation_and_source_metadata() -> None:
    """样本检查点只应保留展示所需的双语来源字段，不能泄漏隐藏判题材料。"""
    sample = EvaluationSampleCheckpoint(
        node_id="node_api",
        task_id="job_api",
        sample_key="HumanEval/7",
        sample_index=6,
        status="failed",
        attempt_count=1,
        input={
            "input": "English prompt",
            "reference": "hidden canonical solution",
            "test": "hidden test",
            "env": "SECRET_ENV",
        },
        result={
            "score": 0.0,
            "prediction": "Model completion",
            "metadata": {
                "input_zh": "中文题目",
                "reference_zh": None,
                "source": "HumanEval",
                "source_key": "HumanEval/7",
                "canonical_solution": "hidden canonical solution",
                "test": "hidden test",
            },
            "raw_secret_result": "never expose",
        },
        last_error={"message": "traceback SECRET_TRACE", "test": "hidden test"},
    )

    response = sample_checkpoint(sample)

    assert response["result"] == {
        "score": 0.0,
        "prediction": "Model completion",
        "metadata": {
            "input_zh": "中文题目",
            "reference_zh": None,
            "source": "HumanEval",
            "source_key": "HumanEval/7",
        },
    }
    assert response["input"] == {"input": "English prompt"}
    assert response["last_error"] is None


def test_create_suite_evaluation_persists_suite_id() -> None:
    """行业套件创建请求应校验 Registry 并写入稳定 suite_id。"""
    service = FakeTaskService(task_fixture())
    status, _ = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        payload={
            "suite_id": "llm-industry-core-v1",
            "dataset": "gsm8k",
            "adapter": "ollama",
            "model": "qwen2.5:0.5b",
            "sample_mode": "all",
        },
    )

    assert status == 202
    assert service.submitted_request is not None
    assert service.submitted_request.suite_id == "llm-industry-core-v1"
    assert service.submitted_request.subject == "all"


def test_get_unknown_evaluation_returns_structured_not_found() -> None:
    """未知任务详情应映射为不含 KeyError 引号的结构化 404。"""
    status, response = call_handler(
        method="GET",
        path="/api/evaluations/missing",
        service=FakeTaskService(task_fixture()),
    )

    assert status == 404
    assert response == {"ok": False, "error": "task not found: missing"}


def test_cancel_terminal_evaluation_returns_conflict() -> None:
    """取消完成任务应返回 409 并保留原终态。"""
    service = FakeTaskService(task_fixture(status="success", with_result=True))
    status, response = call_handler(
        method="POST",
        path="/api/evaluations/job_api/cancel",
        service=service,
    )

    assert status == 409
    assert response == {"ok": False, "error": "task is already success"}


def test_create_evaluation_rejects_invalid_sample_limit() -> None:
    """自定义数量非正整数时应在入队前返回 400。"""
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=FakeTaskService(task_fixture()),
        payload={
            "dataset": "gsm8k",
            "adapter": "oracle",
            "model": "local-test",
            "base_url": "http://127.0.0.1:11434",
            "sample_mode": "custom",
            "limit": 0,
        },
    )

    assert status == 400
    assert response == {"ok": False, "error": "limit must be a positive integer"}


def test_create_evaluation_rejects_non_object_json() -> None:
    """数组等合法 JSON 非对象正文应返回 400，而不能让处理器连接中断。"""
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=FakeTaskService(task_fixture()),
        payload=["not", "an", "object"],
    )

    assert status == 400
    assert response == {"ok": False, "error": "request body must be a JSON object"}


def _provider_repository(tmp_path: Path) -> ModelProviderRepository:
    """构造凭据与数据库均位于临时目录的 API 测试仓储。

    Args:
        tmp_path: pytest 为当前测试提供的隔离目录。

    Returns:
        不会读取用户真实模型服务商配置的仓储。
    """
    return ModelProviderRepository(
        tmp_path / "providers.sqlite3",
        CredentialCipher.from_runtime(tmp_path, env={}),
    )


def test_model_provider_crud_never_returns_secret(tmp_path: Path) -> None:
    """服务商创建、列表、空密钥更新和删除响应都不得回显完整凭据。"""
    repository = _provider_repository(tmp_path)
    service = FakeTaskService(task_fixture())
    secret = "sk-provider-secret"

    list_status, initial = call_handler(
        method="GET",
        path="/api/model-providers",
        service=service,
        provider_repository=repository,
    )
    create_status, created = call_handler(
        method="POST",
        path="/api/model-providers",
        service=service,
        provider_repository=repository,
        payload={
            "name": "Internal Gateway",
            "base_url": "https://gateway.example.com/v1/",
            "api_key": secret,
        },
    )
    provider_id = created["provider"]["id"]
    update_status, updated = call_handler(
        method="PUT",
        path=f"/api/model-providers/{provider_id}",
        service=service,
        provider_repository=repository,
        payload={"name": "Renamed Gateway", "api_key": ""},
    )
    preserved_key = repository.resolve_api_key(provider_id)
    delete_status, deleted = call_handler(
        method="DELETE",
        path=f"/api/model-providers/{provider_id}",
        service=service,
        provider_repository=repository,
    )

    assert list_status == 200
    assert initial["providers"][0] == {
        "id": "deepseek",
        "name": "DeepSeek",
        "kind": "builtin",
        "base_url": "https://api.deepseek.com",
        "key_configured": False,
        "key_hint": None,
        "created_at": None,
        "updated_at": None,
    }
    assert create_status == 201
    assert update_status == delete_status == 200
    assert updated["provider"]["name"] == "Renamed Gateway"
    assert preserved_key == secret
    assert deleted == {"ok": True, "provider_id": provider_id, "reset": False}
    assert secret not in json.dumps([initial, created, updated, deleted], ensure_ascii=False)


def test_delete_builtin_provider_restores_default(tmp_path: Path) -> None:
    """删除内置项应清除凭据和地址覆盖，但固定预设本身仍然可选。"""
    repository = _provider_repository(tmp_path)
    repository.save(
        "deepseek",
        name="DeepSeek",
        base_url="https://proxy.example.com/v1",
        api_key="sk-reset",
    )

    status, response = call_handler(
        method="DELETE",
        path="/api/model-providers/deepseek",
        service=FakeTaskService(task_fixture()),
        provider_repository=repository,
    )

    assert status == 200
    assert response == {"ok": True, "provider_id": "deepseek", "reset": True}
    assert repository.get("deepseek").base_url == "https://api.deepseek.com"
    assert repository.get("deepseek").key_configured is False


def test_provider_mutation_rejects_remote_client_and_unsafe_url(tmp_path: Path) -> None:
    """非回环客户端不能写凭据，远程明文 HTTP 地址也必须在保存前拒绝。"""
    repository = _provider_repository(tmp_path)
    service = FakeTaskService(task_fixture())
    payload = {
        "name": "Unsafe",
        "base_url": "http://api.example.com/v1",
        "api_key": "sk-secret",
    }

    remote_status, remote = call_handler(
        method="POST",
        path="/api/model-providers",
        service=service,
        provider_repository=repository,
        payload=payload,
        client_host="203.0.113.10",
    )
    local_status, local = call_handler(
        method="POST",
        path="/api/model-providers",
        service=service,
        provider_repository=repository,
        payload=payload,
    )

    assert remote_status == 403
    assert remote == {"ok": False, "error": "provider credentials are loopback-only"}
    assert local_status == 400
    assert "HTTPS" in local["error"]
    assert len(repository.list()) == 3


def test_provider_test_discovers_models_from_saved_credential(
    tmp_path: Path,
) -> None:
    """连通性测试应只使用已保存凭据并返回排序后的模型列表。"""
    repository = _provider_repository(tmp_path)
    repository.save(
        "kimi",
        name="Kimi",
        base_url="https://api.moonshot.ai/v1",
        api_key="sk-kimi",
    )

    with patch(
        "evalhub.server.discover_models",
        return_value=["kimi-k3", "kimi-k3-fast"],
    ) as discover:
        status, response = call_handler(
            method="POST",
            path="/api/model-providers/kimi/test",
            service=FakeTaskService(task_fixture()),
            provider_repository=repository,
        )

    assert status == 200
    assert response == {"ok": True, "models": ["kimi-k3", "kimi-k3-fast"]}
    discover.assert_called_once_with("https://api.moonshot.ai/v1", "sk-kimi")
    assert "sk-kimi" not in json.dumps(response)


def test_create_api_evaluation_uses_provider_snapshot_without_key(tmp_path: Path) -> None:
    """API 模型任务应忽略客户端冲突地址并只持久化服务商引用和仓储地址。"""
    repository = _provider_repository(tmp_path)
    repository.save(
        "deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key="sk-evaluation-secret",
    )
    service = FakeTaskService(task_fixture())

    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        provider_repository=repository,
        payload={
            "dataset": "gsm8k",
            "adapter": "openai-compatible",
            "provider_id": "deepseek",
            "model": "deepseek-v4-pro",
            "base_url": "https://attacker.example.com/v1",
            "sample_mode": "quick",
        },
    )

    assert status == 202
    assert response["ok"] is True
    assert service.submitted_request is not None
    assert service.submitted_request.provider_id == "deepseek"
    assert service.submitted_request.base_url == "https://api.deepseek.com"
    request_payload = asdict(service.submitted_request)
    assert all("api_key" not in key for key in request_payload)
    assert "sk-evaluation-secret" not in json.dumps(response)


def test_create_api_evaluation_requires_configured_provider(tmp_path: Path) -> None:
    """缺少服务商 ID 或未配置凭据时，API 模型任务必须在入队前返回 400。"""
    repository = _provider_repository(tmp_path)
    service = FakeTaskService(task_fixture())
    base_payload = {
        "dataset": "gsm8k",
        "adapter": "openai-compatible",
        "model": "deepseek-v4-pro",
        "sample_mode": "quick",
    }

    missing_status, missing = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        provider_repository=repository,
        payload=base_payload,
    )
    unconfigured_status, unconfigured = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        provider_repository=repository,
        payload={**base_payload, "provider_id": "deepseek"},
    )

    assert missing_status == unconfigured_status == 400
    assert missing["error"] == "provider_id is required for openai-compatible adapter"
    assert unconfigured["error"] == "model provider deepseek has no API Key"
    assert service.submitted_request is None
