"""验证内置 Coding Mini 的隐藏校验、进度和六维能力聚合。"""

import json
from pathlib import Path

from evalhub.agent.codex import AgentTraceEvent, CodexAgentError, CodexRunResult, TraceCallback
from evalhub.benchmarks.coding_mini import CAPABILITY_DIMENSIONS, run_codex_agent_benchmark


class EditingFakeRunner:
    """按样本工作区写入确定性修复，用来隔离真实 Codex 与 Ollama。"""

    def __init__(self, *, skip_sample: str | None = None) -> None:
        """配置一个可选的不修复样本，以验证隐藏 Verifier 会拒绝错误代码。"""
        self.skip_sample = skip_sample
        self.workspaces: list[Path] = []

    def version(self) -> str:
        """返回测试固定 CLI 版本，避免调用用户机器上的 Codex。"""
        return "codex-cli test"

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> CodexRunResult:
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
                    "actor": "codex",
                    "message": "edit file",
                    "payload": {"tool_name": "file_change", "command": "edit file"},
                }
            )
        return CodexRunResult("fixed", 2, 0, 0.01, self.version(), tool_call_count=1)

    @staticmethod
    def _apply_fix(sample_id: str, workspace: Path) -> None:
        """写入每个内置样本的预期行为，但不接触生产 Verifier。"""
        if sample_id == "pricing_total":
            (workspace / "pricing.py").write_text(
                "def total_with_tax(prices, tax_rate):\n"
                "    return round(sum(prices) * (1 + tax_rate), 2)\n",
                encoding="utf-8",
            )
        elif sample_id == "slug_normalization":
            (workspace / "slug.py").write_text(
                "import re\n\n"
                "def normalize_slug(value):\n"
                "    words = re.findall(r'[a-z0-9]+', value.lower())\n"
                "    return '-'.join(words)\n",
                encoding="utf-8",
            )
        elif sample_id == "inventory_reservation":
            (workspace / "inventory.py").write_text(
                "def reserve(stock, item, quantity):\n"
                "    if quantity <= 0 or stock.get(item, 0) < quantity:\n"
                "        return False\n"
                "    stock[item] -= quantity\n"
                "    return True\n",
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
    ) -> CodexRunResult:
        """保留初始工作区并返回存在的最终消息。"""
        del instruction, model, base_url, timeout_seconds, on_event
        self.workspaces.append(workspace)
        return CodexRunResult("only narration", 1, 0, 0.01, self.version())


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
    ) -> CodexRunResult:
        """写入无效注释，使 Git 有变化但隐藏校验仍失败。"""
        del instruction, model, base_url, timeout_seconds, on_event
        self.workspaces.append(workspace)
        target = workspace / "pricing.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "# attempted fix\n",
            encoding="utf-8",
        )
        return CodexRunResult("wrong edit", 2, 0, 0.01, self.version(), tool_call_count=1)


class ErrorFakeRunner(EditingFakeRunner):
    """模拟 Codex 无最终消息等运行时故障。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> CodexRunResult:
        """不修改工作区并抛出稳定 Runner 错误。"""
        del instruction, model, base_url, workspace, timeout_seconds, on_event
        raise CodexAgentError("codex produced no final message")


def test_coding_mini_uses_hidden_verifier_and_builds_six_dimensions(tmp_path: Path) -> None:
    """全部修复通过时应报告完整进度、六个满分维度和隔离工作区。"""
    runner = EditingFakeRunner()
    progress: list[tuple[int, int]] = []

    result = run_codex_agent_benchmark(
        job_id="job_agent",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        limit=3,
        on_progress=lambda completed, total: progress.append((completed, total)),
        runner=runner,
        runtime_root=tmp_path,
    )

    # Fake 只负责修改文件，真实隐藏 Verifier 决定样本是否通过。
    assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert result["passed_samples"] == 3
    capability_report = result["capability_report"]
    assert len(capability_report["dimensions"]) == 6
    assert all(item["score"] == 1.0 for item in capability_report["dimensions"])
    assert [item["key"] for item in capability_report["dimensions"]] == [
        key for key, _label in CAPABILITY_DIMENSIONS
    ]
    assert all(workspace.is_relative_to(tmp_path / "job_agent") for workspace in runner.workspaces)


def test_coding_mini_scores_failed_sample_by_declared_capability_weights(
    tmp_path: Path,
) -> None:
    """一个样本未修复时应由隐藏校验判失败，并只降低它覆盖的能力维度。"""
    result = run_codex_agent_benchmark(
        job_id="job_partial",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        limit=3,
        runner=EditingFakeRunner(skip_sample="pricing_total"),
        runtime_root=tmp_path,
    )

    # 定价样本失败不应影响仅由其他样本覆盖的工具、验证和稳健性能力。
    assert result["passed_samples"] == 2
    assert result["failed_sample_ids"] == ["pricing_total"]
    dimension_scores = {
        item["key"]: item["score"] for item in result["capability_report"]["dimensions"]
    }
    assert dimension_scores == {
        "planning": 0.0,
        "code_understanding": 0.3333,
        "implementation": 0.6364,
        "tool_use": 1.0,
        "verification": 1.0,
        "robustness": 1.0,
    }
    failed_result = result["sample_results"][0]
    assert failed_result["status"] == "failed"
    assert failed_result["verifier_message"]
    failed_example = result["failed_examples"][0]
    assert failed_example["input"] == "pricing_total"
    assert failed_example["prediction"] == "fixed"
    assert failed_example["reference"] == "hidden verifier passed"


def test_coding_mini_classifies_passed_no_action_wrong_solution_and_runtime_error(
    tmp_path: Path,
) -> None:
    """文件证据和 Verifier 应稳定区分四类 Agent 样本结果。"""
    cases = (
        ("passed", EditingFakeRunner(), ["pricing.py"], True, True, 1),
        ("no_action", NoActionFakeRunner(), [], True, False, 0),
        ("wrong_solution", WrongEditFakeRunner(), ["pricing.py"], True, False, 1),
        ("runtime_error", ErrorFakeRunner(), [], False, False, 0),
    )

    # 每个 case 使用独立任务目录，避免 Git 文件副作用污染其他分类。
    for index, (outcome, runner, changed_files, final_message, verifier, tool_count) in enumerate(
        cases
    ):
        result = run_codex_agent_benchmark(
            job_id=f"job_outcome_{index}",
            model="local-test",
            base_url="http://127.0.0.1:11434",
            limit=1,
            runner=runner,
            runtime_root=tmp_path,
        )
        diagnostics = result["sample_results"][0]["diagnostics"]
        assert diagnostics == {
            "outcome": outcome,
            "tool_call_count": tool_count,
            "changed_files": changed_files,
            "final_message_present": final_message,
            "verifier_passed": verifier,
        }


def test_coding_mini_emits_auditable_stages_without_hidden_verifier_code(
    tmp_path: Path,
) -> None:
    """实时事件应包含题目和外部动作，但不得提前泄漏隐藏断言源码。"""
    events: list[AgentTraceEvent] = []

    run_codex_agent_benchmark(
        job_id="job_trace",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        limit=1,
        runner=EditingFakeRunner(),
        runtime_root=tmp_path,
        on_trace=events.append,
    )

    assert [event["event_type"] for event in events] == [
        "sample_started",
        "tool_started",
        "workspace_changed",
        "verifier_finished",
        "sample_finished",
    ]
    assert events[1]["payload"]["sample_id"] == "pricing_total"
    serialized = json.dumps(events, ensure_ascii=False)
    assert "Fix pricing.total_with_tax" in serialized
    assert "assert total_with_tax" not in serialized
