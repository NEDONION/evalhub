"""验证内置 Coding Mini 的隐藏校验、进度和六维能力聚合。"""

from pathlib import Path

from evalhub.agent.codex import CodexRunResult
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
    ) -> CodexRunResult:
        """根据工作区样本标识写入正确实现，并返回稳定的 Agent 元数据。"""
        del instruction, model, base_url, timeout_seconds
        self.workspaces.append(workspace)
        sample_id = workspace.parent.name
        if sample_id != self.skip_sample:
            self._apply_fix(sample_id, workspace)
        return CodexRunResult("fixed", 2, 0, 0.01, self.version())

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
