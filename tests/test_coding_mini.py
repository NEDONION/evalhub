"""验证内置 Coding Mini 的隐藏校验、进度和六维能力聚合。"""

import json
from collections import Counter
from pathlib import Path

import pytest

import evalhub.benchmarks.coding_mini as coding_mini_module
from evalhub.agent.pi import AgentTraceEvent, PiAgentError, PiRunResult, TraceCallback
from evalhub.benchmarks.coding_mini import (
    CAPABILITY_DIMENSIONS,
    _create_workspace,
    _run_protocol_preflight,
    _run_sample,
    _select_samples,
    coding_mini_samples,
    run_pi_agent_benchmark,
)


def test_coding_mini_catalog_has_two_explained_samples_per_difficulty() -> None:
    """内置题集应提供稳定、无重复且每档两道的三级难度样本。"""
    samples = coding_mini_samples()

    assert len(samples) == 6
    assert len({sample.id for sample in samples}) == 6
    assert Counter(sample.difficulty for sample in samples) == {
        "easy": 2,
        "medium": 2,
        "hard": 2,
    }
    assert all(sample.difficulty_reason.strip() for sample in samples)


@pytest.mark.parametrize(
    ("difficulty", "expected_ids"),
    [
        (
            "all",
            [
                "path_normalization",
                "config_precedence",
                "pagination_merge",
                "cache_expiry",
                "reservation_idempotency",
                "async_worker_cleanup",
            ],
        ),
        ("easy", ["path_normalization", "config_precedence"]),
        ("medium", ["pagination_merge", "cache_expiry"]),
        ("hard", ["reservation_idempotency", "async_worker_cleanup"]),
    ],
)
def test_coding_mini_selects_stable_difficulty_groups(
    difficulty: str,
    expected_ids: list[str],
) -> None:
    """全部和单档选择都应返回固定顺序，保证报告可复现。"""
    assert [sample.id for sample in _select_samples(difficulty)] == expected_ids


def test_coding_mini_rejects_unknown_difficulty() -> None:
    """未知难度不得静默退化为全部题目。"""
    with pytest.raises(ValueError, match="difficulty must be one of"):
        _select_samples("expert")


class EditingFakeRunner:
    """按样本工作区写入确定性修复，用来隔离真实 Pi 与 Ollama。"""

    def __init__(self, *, skip_sample: str | None = None) -> None:
        """配置一个可选的不修复样本，以验证隐藏 Verifier 会拒绝错误代码。"""
        self.skip_sample = skip_sample
        self.workspaces: list[Path] = []

    def version(self) -> str:
        """返回测试固定 CLI 版本，避免调用用户机器上的 Pi。"""
        return "pi-cli test"

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """根据工作区样本标识写入正确实现，并返回稳定的 Agent 元数据。"""
        del instruction, model, base_url, timeout_seconds
        self.workspaces.append(workspace)
        sample_id = workspace.parent.name
        if sample_id != self.skip_sample:
            self._apply_fix(sample_id, workspace)
        if on_event is not None:
            on_event(
                {
                    "event_type": "tool_started",
                    "actor": "pi",
                    "message": "edit file",
                    "payload": {"tool_name": "file_change", "command": "edit file"},
                }
            )
        return PiRunResult("fixed", 2, 0, 0.01, self.version(), tool_call_count=1)

    @staticmethod
    def _apply_fix(sample_id: str, workspace: Path) -> None:
        """写入每个内置样本的预期行为，但不接触生产 Verifier。"""
        if sample_id == "protocol_preflight":
            (workspace / "protocol_probe.txt").write_text("OK\n", encoding="utf-8")
        elif sample_id == "path_normalization":
            (workspace / "paths.py").write_text(
                "import posixpath\n\n"
                "def normalize_user_path(root, value):\n"
                "    root = posixpath.normpath(root)\n"
                "    candidate = posixpath.normpath(\n"
                "        value if posixpath.isabs(value) else posixpath.join(root, value)\n"
                "    )\n"
                "    return candidate if posixpath.commonpath([root, candidate]) == root "
                "else None\n",
                encoding="utf-8",
            )
        elif sample_id == "config_precedence":
            (workspace / "config.py").write_text(
                "def resolve_setting(name, arguments, environment, file_values, default):\n"
                "    for source in (arguments, environment, file_values):\n"
                "        if name in source:\n"
                "            return source[name]\n"
                "    return default\n",
                encoding="utf-8",
            )
        elif sample_id == "pagination_merge":
            (workspace / "pagination.py").write_text(
                "from client import fetch_page\n\n"
                "def collect_records(pages, cursor='first'):\n"
                "    records = []\n"
                "    seen_cursors = set()\n"
                "    seen_ids = set()\n"
                "    while cursor is not None:\n"
                "        if cursor in seen_cursors:\n"
                "            raise ValueError('repeated cursor')\n"
                "        seen_cursors.add(cursor)\n"
                "        page = fetch_page(pages, cursor)\n"
                "        for record in page['records']:\n"
                "            if record['id'] not in seen_ids:\n"
                "                records.append(record)\n"
                "                seen_ids.add(record['id'])\n"
                "        cursor = page.get('next_cursor')\n"
                "    return records\n",
                encoding="utf-8",
            )
        elif sample_id == "cache_expiry":
            (workspace / "cache.py").write_text(
                "class Cache:\n"
                "    def __init__(self, clock):\n"
                "        self.clock = clock\n"
                "        self.entries = {}\n\n"
                "    def put(self, key, value, ttl):\n"
                "        if ttl <= 0:\n"
                "            self.entries.pop(key, None)\n"
                "            return\n"
                "        self.entries[key] = (value, self.clock() + ttl)\n\n"
                "    def get(self, key):\n"
                "        entry = self.entries.get(key)\n"
                "        if entry is None:\n"
                "            return None\n"
                "        value, expires_at = entry\n"
                "        if self.clock() >= expires_at:\n"
                "            self.entries.pop(key, None)\n"
                "            return None\n"
                "        return value\n\n"
                "    def purge_expired(self):\n"
                "        now = self.clock()\n"
                "        expired = [key for key, (_, expiry) in self.entries.items() "
                "if now >= expiry]\n"
                "        for key in expired:\n"
                "            del self.entries[key]\n"
                "        return len(expired)\n",
                encoding="utf-8",
            )
        elif sample_id == "reservation_idempotency":
            (workspace / "reservations.py").write_text(
                "from collections import Counter\n"
                "from audit import record\n\n"
                "def reserve(stock, requests, idempotency_key, audit_log, processed):\n"
                "    if idempotency_key in processed:\n"
                "        return True\n"
                "    totals = Counter()\n"
                "    for item, quantity in requests:\n"
                "        if quantity <= 0:\n"
                "            return False\n"
                "        totals[item] += quantity\n"
                "    if any(stock.get(item, 0) < quantity for item, quantity in totals.items()):\n"
                "        return False\n"
                "    for item, quantity in totals.items():\n"
                "        stock[item] -= quantity\n"
                "    processed.add(idempotency_key)\n"
                "    record(audit_log, idempotency_key, dict(totals))\n"
                "    return True\n",
                encoding="utf-8",
            )
        elif sample_id == "async_worker_cleanup":
            (workspace / "worker.py").write_text(
                "async def run_once(queue, open_resource, handle):\n"
                "    item = await queue.get()\n"
                "    resource = None\n"
                "    try:\n"
                "        resource = await open_resource()\n"
                "        return await handle(resource, item)\n"
                "    finally:\n"
                "        try:\n"
                "            if resource is not None:\n"
                "                await resource.aclose()\n"
                "        finally:\n"
                "            queue.task_done()\n",
                encoding="utf-8",
            )


class NoActionFakeRunner(EditingFakeRunner):
    """返回文字但不调用工具或修改文件，用于验证 no_action 分类。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """保留初始工作区并返回存在的最终消息。"""
        del instruction, model, base_url, timeout_seconds, on_event
        self.workspaces.append(workspace)
        return PiRunResult("only narration", 1, 0, 0.01, self.version())


class WrongEditFakeRunner(EditingFakeRunner):
    """修改受控文件但保留缺陷，用于验证 wrong_solution 分类。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """写入无效注释，使 Git 有变化但隐藏校验仍失败。"""
        del instruction, model, base_url, timeout_seconds, on_event
        self.workspaces.append(workspace)
        target = workspace / "paths.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "# attempted fix\n",
            encoding="utf-8",
        )
        return PiRunResult("wrong edit", 2, 0, 0.01, self.version(), tool_call_count=1)


class ErrorFakeRunner(EditingFakeRunner):
    """模拟 Pi 无最终消息等运行时故障。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """不修改工作区并抛出稳定 Runner 错误。"""
        del instruction, model, base_url, workspace, timeout_seconds, on_event
        raise PiAgentError("pi produced no final message")


class ErrorAfterValidEditFakeRunner(EditingFakeRunner):
    """模拟 Pi 已完成正确修改但缺少最终自然语言消息。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """写入可通过隐藏校验的实现后抛出 Runner 错误。

        参数：
            instruction: 当前编码任务说明，本替身不解析其内容。
            model: 当前基模名称，本替身不发起模型请求。
            base_url: 模型服务地址，本替身不访问网络。
            workspace: 允许修改的独立样本工作区。
            timeout_seconds: 运行时间预算，本替身立即结束。
            on_event: 可选过程事件回调，本替身不产生 Pi 事件。

        异常：
            PiAgentError: 始终模拟缺失最终消息的运行边界错误。
        """
        del instruction, model, base_url, timeout_seconds, on_event
        EditingFakeRunner._apply_fix("path_normalization", workspace)
        raise PiAgentError("pi produced no final message")


class ErrorAfterToolFakeRunner(EditingFakeRunner):
    """模拟工具调用失败后 Pi 没有产生最终消息。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """发出一对失败工具事件后抛出运行错误。

        参数：
            instruction: 当前任务说明，本替身不解析。
            model: 当前模型名称，本替身不发起请求。
            base_url: 当前模型服务地址，本替身不访问网络。
            workspace: 当前样本工作区，本替身保持其不变。
            timeout_seconds: 当前超时预算，本替身立即结束。
            on_event: 接收标准化 Pi 事件的可选回调。

        异常：
            PiAgentError: 始终模拟工具失败后的运行边界错误。
        """
        del instruction, model, base_url, workspace, timeout_seconds
        if on_event is not None:
            on_event(
                {
                    "event_type": "tool_started",
                    "actor": "pi",
                    "message": "edit missing.py",
                    "payload": {"tool_name": "edit", "command": "missing.py"},
                }
            )
            on_event(
                {
                    "event_type": "tool_finished",
                    "actor": "pi",
                    "message": "edit",
                    "payload": {
                        "tool_name": "edit",
                        "command": "",
                        "output": "file not found",
                        "is_error": True,
                    },
                }
            )
        raise PiAgentError("pi produced no final message")


class DegradedProtocolFakeRunner(EditingFakeRunner):
    """模拟工具已成功写入 marker，但 Pi 缺少最终消息。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """写入精确 marker、发送工具事件并抛出协议结束错误。

        参数：
            instruction: 预检说明，本替身不解析。
            model: 当前模型名称，本替身不发起请求。
            base_url: 当前模型服务地址，本替身不访问网络。
            workspace: 预检工作区，用于写入精确 marker。
            timeout_seconds: 当前超时预算，本替身立即结束。
            on_event: 接收标准化 Pi 事件的可选回调。

        异常：
            PiAgentError: 始终模拟缺少最终消息。
        """
        del instruction, model, base_url, timeout_seconds
        (workspace / "protocol_probe.txt").write_text("OK\n", encoding="utf-8")
        if on_event is not None:
            on_event(
                {
                    "event_type": "tool_started",
                    "actor": "pi",
                    "message": "write protocol_probe.txt",
                    "payload": {"tool_name": "write", "command": "protocol_probe.txt"},
                }
            )
        raise PiAgentError("pi produced no final message")


class InfrastructureErrorFakeRunner(EditingFakeRunner):
    """模拟 macOS Seatbelt 无法启用的确定性基础设施错误。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """不执行模型并抛出带稳定分类的执行器错误。

        参数：
            instruction: 当前任务或预检说明，本替身不解析。
            model: 当前模型名称，本替身不发起请求。
            base_url: 当前模型服务地址，本替身不访问网络。
            workspace: 当前隔离工作区，本替身不修改。
            timeout_seconds: 当前预算，本替身立即失败。
            on_event: 可选过程回调，本替身不产生事件。

        异常：
            PiAgentError: 始终报告执行器未就绪。
        """
        del instruction, model, base_url, workspace, timeout_seconds, on_event
        raise PiAgentError("sandbox-exec unavailable", error_type="executor_not_ready")


def test_coding_mini_uses_hidden_verifier_and_builds_six_dimensions(tmp_path: Path) -> None:
    """全部修复通过时应报告完整进度、六个满分维度和隔离工作区。"""
    runner = EditingFakeRunner()
    progress: list[tuple[int, int]] = []

    result = run_pi_agent_benchmark(
        job_id="job_agent",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        difficulty="all",
        on_progress=lambda completed, total: progress.append((completed, total)),
        runner=runner,
        runtime_root=tmp_path,
    )

    # Fake 只负责修改文件，真实隐藏 Verifier 决定样本是否通过。
    assert progress == [(0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
    assert result["passed_samples"] == 6
    assert result["protocol_preflight"]["status"] == "compatible"
    assert result["benchmark_version"] == "coding-mini-v3"
    assert result["requested_difficulty"] == "all"
    assert result["difficulty_report"] == [
        {"difficulty": "easy", "total": 2, "passed": 2, "pass_rate": 1.0},
        {"difficulty": "medium", "total": 2, "passed": 2, "pass_rate": 1.0},
        {"difficulty": "hard", "total": 2, "passed": 2, "pass_rate": 1.0},
    ]
    assert [
        (item["sample_id"], item["difficulty"]) for item in result["sample_results"]
    ] == [
        ("path_normalization", "easy"),
        ("config_precedence", "easy"),
        ("pagination_merge", "medium"),
        ("cache_expiry", "medium"),
        ("reservation_idempotency", "hard"),
        ("async_worker_cleanup", "hard"),
    ]
    capability_report = result["capability_report"]
    assert len(capability_report["dimensions"]) == 6
    assert all(item["score"] == 1.0 for item in capability_report["dimensions"])
    assert [item["key"] for item in capability_report["dimensions"]] == [
        key for key, _label in CAPABILITY_DIMENSIONS
    ]
    assert all(workspace.is_relative_to(tmp_path / "job_agent") for workspace in runner.workspaces)


def test_coding_mini_aggregates_sample_execution_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务结果应汇总正式样本过程指标，但不把预检计入六题统计。"""
    ticks = iter((0.0, 1.0) * 7)
    monkeypatch.setattr(coding_mini_module, "monotonic", lambda: next(ticks))

    result = run_pi_agent_benchmark(
        job_id="job_execution_summary",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        difficulty="all",
        runner=EditingFakeRunner(),
        runtime_root=tmp_path,
    )

    assert result["execution_summary"] == {
        "total_tool_calls": 6,
        "average_tool_calls": 1.0,
        "total_tool_errors": 0,
        "total_wall_time_seconds": 6.0,
        "average_wall_time_seconds": 1.0,
        "max_wall_time_seconds": 1.0,
        "total_changed_files": 6,
        "outcome_counts": {
            "passed": 6,
            "no_action": 0,
            "wrong_solution": 0,
            "runtime_error": 0,
            "protocol_error": 0,
        },
    }


def test_coding_mini_preflight_distinguishes_protocol_states(tmp_path: Path) -> None:
    """预检应区分正常结束、工具已执行但结束异常和完全无工具三种状态。"""
    cases = (
        ("compatible", EditingFakeRunner()),
        ("degraded", DegradedProtocolFakeRunner()),
        ("incompatible", NoActionFakeRunner()),
    )

    for index, (expected, runner) in enumerate(cases):
        job_root = tmp_path / f"preflight_{index}"
        job_root.mkdir()
        result = _run_protocol_preflight(
            job_root=job_root,
            model="local-test",
            base_url="http://127.0.0.1:11434",
            runner=runner,
            on_trace=None,
        )
        assert result["status"] == expected


def test_coding_mini_does_not_score_agent_infrastructure_failure(tmp_path: Path) -> None:
    """Seatbelt 等执行器故障应阻塞整套评测，不能生成模型零分。"""
    with pytest.raises(PiAgentError, match="sandbox-exec unavailable") as raised:
        run_pi_agent_benchmark(
            job_id="job_infrastructure_failure",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            difficulty="all",
            runner=InfrastructureErrorFakeRunner(),
            runtime_root=tmp_path,
        )

    assert raised.value.error_type == "executor_not_ready"


def test_coding_mini_classifies_incompatible_no_action_as_protocol_error(
    tmp_path: Path,
) -> None:
    """预检不兼容且正式样本无动作时应归因协议，而非笼统 no_action。"""
    result = run_pi_agent_benchmark(
        job_id="job_protocol_error",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        difficulty="easy",
        runner=NoActionFakeRunner(),
        runtime_root=tmp_path,
    )

    assert result["protocol_preflight"]["status"] == "incompatible"
    assert [item["diagnostics"]["outcome"] for item in result["sample_results"]] == [
        "protocol_error",
        "protocol_error",
    ]


def test_coding_mini_scores_failed_sample_by_declared_capability_weights(
    tmp_path: Path,
) -> None:
    """一个样本未修复时应由隐藏校验判失败，并只降低它覆盖的能力维度。"""
    result = run_pi_agent_benchmark(
        job_id="job_partial",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        difficulty="easy",
        runner=EditingFakeRunner(skip_sample="path_normalization"),
        runtime_root=tmp_path,
    )

    # 路径样本失败不应影响仅由配置样本覆盖的工具、验证和稳健性能力。
    assert result["passed_samples"] == 1
    assert result["failed_sample_ids"] == ["path_normalization"]
    dimension_scores = {
        item["key"]: item["score"] for item in result["capability_report"]["dimensions"]
    }
    assert dimension_scores == {
        "planning": 0.0,
        "code_understanding": 0.0,
        "implementation": 0.0,
        "tool_use": 1.0,
        "verification": 1.0,
        "robustness": 1.0,
    }
    failed_result = result["sample_results"][0]
    assert failed_result["status"] == "failed"
    assert failed_result["difficulty"] == "easy"
    assert failed_result["verifier_message"]
    failed_example = result["failed_examples"][0]
    assert failed_example["input"] == "path_normalization"
    assert failed_example["prediction"] == "fixed"
    assert failed_example["reference"] == "hidden verifier passed"
    assert failed_example["difficulty"] == "easy"


def test_coding_mini_classifies_passed_no_action_wrong_solution_and_runtime_error(
    tmp_path: Path,
) -> None:
    """文件证据和 Verifier 应稳定区分四类 Agent 样本结果。"""
    cases = (
        ("passed", EditingFakeRunner(), ["paths.py"], True, True, 1),
        ("no_action", NoActionFakeRunner(), [], True, False, 0),
        ("wrong_solution", WrongEditFakeRunner(), ["paths.py"], True, False, 1),
        ("runtime_error", ErrorFakeRunner(), [], False, False, 0),
    )

    # 每个 case 使用独立任务目录，避免 Git 文件副作用污染其他分类。
    for index, (outcome, runner, changed_files, final_message, verifier, tool_count) in enumerate(
        cases
    ):
        sample = coding_mini_samples()[0]
        workspace = _create_workspace(tmp_path / f"job_outcome_{index}", sample)
        sample_result = _run_sample(
            sample=sample,
            workspace=workspace,
            model="local-test",
            base_url="http://127.0.0.1:11434",
            runner=runner,
            on_trace=None,
        )
        diagnostics = dict(sample_result["diagnostics"])
        assert float(diagnostics.pop("wall_time_seconds")) >= 0
        assert diagnostics == {
            "outcome": outcome,
            "tool_call_count": tool_count,
            "tool_error_count": 0,
            "changed_files": changed_files,
            "final_message_present": final_message,
            "verifier_passed": verifier,
        }


def test_coding_mini_verifies_final_workspace_after_runner_error(tmp_path: Path) -> None:
    """Pi 缺少最终自述时仍应按工作区隐藏校验结果计分。"""
    events: list[AgentTraceEvent] = []
    sample = coding_mini_samples()[0]
    workspace = _create_workspace(tmp_path / "job_runner_error", sample)

    # Agent 最终消息不是评分依据；正确文件即使伴随 Runner 警告也必须获得通过分。
    result = _run_sample(
        sample=sample,
        workspace=workspace,
        model="local-test",
        base_url="http://127.0.0.1:11434",
        runner=ErrorAfterValidEditFakeRunner(),
        on_trace=events.append,
    )

    assert result["status"] == "success"
    assert result["score"] == 1.0
    assert result["verifier_message"] == "hidden verifier passed"
    diagnostics = dict(result["diagnostics"])
    assert float(diagnostics.pop("wall_time_seconds")) >= 0
    assert diagnostics == {
        "outcome": "passed",
        "tool_call_count": 0,
        "tool_error_count": 0,
        "changed_files": ["paths.py"],
        "final_message_present": False,
        "verifier_passed": True,
    }
    assert [event["event_type"] for event in events] == [
        "sample_started",
        "runner_error",
        "workspace_changed",
        "verifier_finished",
        "sample_finished",
    ]


def test_coding_mini_preserves_process_metrics_after_runner_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner 抛错时也应保留已观察工具、工具错误和实际耗时。"""
    ticks = iter((10.0, 12.5))
    monkeypatch.setattr(coding_mini_module, "monotonic", lambda: next(ticks))
    sample = coding_mini_samples()[0]
    workspace = _create_workspace(tmp_path / "job_process_error", sample)

    result = _run_sample(
        sample=sample,
        workspace=workspace,
        model="local-test",
        base_url="http://127.0.0.1:11434",
        runner=ErrorAfterToolFakeRunner(),
        on_trace=None,
    )

    assert result["wall_time_seconds"] == 2.5
    assert result["diagnostics"] == {
        "outcome": "runtime_error",
        "tool_call_count": 1,
        "tool_error_count": 1,
        "changed_files": [],
        "wall_time_seconds": 2.5,
        "final_message_present": False,
        "verifier_passed": False,
    }


def test_coding_mini_emits_auditable_stages_without_hidden_verifier_code(
    tmp_path: Path,
) -> None:
    """实时事件应包含题目和外部动作，但不得提前泄漏隐藏断言源码。"""
    events: list[AgentTraceEvent] = []

    run_pi_agent_benchmark(
        job_id="job_trace",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        difficulty="easy",
        runner=EditingFakeRunner(),
        runtime_root=tmp_path,
        on_trace=events.append,
    )

    assert [event["event_type"] for event in events] == [
        "tool_started",
        "sample_started",
        "tool_started",
        "workspace_changed",
        "verifier_finished",
        "sample_finished",
        "sample_started",
        "tool_started",
        "workspace_changed",
        "verifier_finished",
        "sample_finished",
    ]
    assert events[0]["payload"]["sample_id"] == "protocol_preflight"
    assert events[2]["payload"]["sample_id"] == "path_normalization"
    assert events[1]["payload"]["difficulty"] == "easy"
    assert events[1]["payload"]["difficulty_reason"] == "单文件路径边界与目录逃逸"
    assert events[1]["message"].startswith("[简单]")
    serialized = json.dumps(events, ensure_ascii=False)
    assert "normalize_user_path" in serialized
    assert "assert normalize_user_path" not in serialized
