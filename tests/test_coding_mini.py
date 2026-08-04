"""验证内置 Coding Mini 的隐藏校验、进度和六维能力聚合。"""

import json
from collections import Counter
from pathlib import Path

import pytest

from evalhub.agent.pi import AgentTraceEvent, PiAgentError, PiRunResult, TraceCallback
from evalhub.benchmarks.coding_mini import (
    CAPABILITY_DIMENSIONS,
    _create_workspace,
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
                "pricing_total",
                "cart_quantity",
                "slug_normalization",
                "inventory_reservation",
                "batch_reservation_atomicity",
                "retry_state_machine",
            ],
        ),
        ("easy", ["pricing_total", "cart_quantity"]),
        ("medium", ["slug_normalization", "inventory_reservation"]),
        ("hard", ["batch_reservation_atomicity", "retry_state_machine"]),
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
        if sample_id == "pricing_total":
            (workspace / "pricing.py").write_text(
                "def total_with_tax(prices, tax_rate):\n"
                "    return round(sum(prices) * (1 + tax_rate), 2)\n",
                encoding="utf-8",
            )
        elif sample_id == "cart_quantity":
            (workspace / "cart.py").write_text(
                "def total_quantity(lines):\n"
                "    return sum(line['quantity'] for line in lines)\n",
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
        elif sample_id == "batch_reservation_atomicity":
            (workspace / "batch.py").write_text(
                "from inventory import reserve\n\n"
                "def reserve_batch(stock, requests):\n"
                "    candidate = stock.copy()\n"
                "    for item, quantity in requests:\n"
                "        if not reserve(candidate, item, quantity):\n"
                "            return False\n"
                "    stock.clear()\n"
                "    stock.update(candidate)\n"
                "    return True\n",
                encoding="utf-8",
            )
        elif sample_id == "retry_state_machine":
            (workspace / "retry.py").write_text(
                "from states import TERMINAL_STATUSES\n\n"
                "def record_failure(job, max_attempts, error):\n"
                "    if job['status'] in TERMINAL_STATUSES:\n"
                "        return False\n"
                "    job['attempts'] += 1\n"
                "    job['last_error'] = error\n"
                "    if job['attempts'] >= max_attempts:\n"
                "        job['status'] = 'failed'\n"
                "        return False\n"
                "    job['status'] = 'retrying'\n"
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
        target = workspace / "pricing.py"
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
    assert result["benchmark_version"] == "coding-mini-v2"
    assert result["requested_difficulty"] == "all"
    assert result["difficulty_report"] == [
        {"difficulty": "easy", "total": 2, "passed": 2, "pass_rate": 1.0},
        {"difficulty": "medium", "total": 2, "passed": 2, "pass_rate": 1.0},
        {"difficulty": "hard", "total": 2, "passed": 2, "pass_rate": 1.0},
    ]
    assert [
        (item["sample_id"], item["difficulty"]) for item in result["sample_results"]
    ] == [
        ("pricing_total", "easy"),
        ("cart_quantity", "easy"),
        ("slug_normalization", "medium"),
        ("inventory_reservation", "medium"),
        ("batch_reservation_atomicity", "hard"),
        ("retry_state_machine", "hard"),
    ]
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
    result = run_pi_agent_benchmark(
        job_id="job_partial",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        difficulty="easy",
        runner=EditingFakeRunner(skip_sample="pricing_total"),
        runtime_root=tmp_path,
    )

    # 定价样本失败不应影响仅由其他样本覆盖的工具、验证和稳健性能力。
    assert result["passed_samples"] == 1
    assert result["failed_sample_ids"] == ["pricing_total"]
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
    assert failed_example["input"] == "pricing_total"
    assert failed_example["prediction"] == "fixed"
    assert failed_example["reference"] == "hidden verifier passed"
    assert failed_example["difficulty"] == "easy"


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
        diagnostics = sample_result["diagnostics"]
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
    assert events[1]["payload"]["sample_id"] == "pricing_total"
    assert events[0]["payload"]["difficulty"] == "easy"
    assert events[0]["payload"]["difficulty_reason"] == "单文件纯函数，缺陷定位直接"
    assert events[0]["message"].startswith("[简单]")
    serialized = json.dumps(events, ensure_ascii=False)
    assert "Fix pricing.total_with_tax" in serialized
    assert "assert total_with_tax" not in serialized
