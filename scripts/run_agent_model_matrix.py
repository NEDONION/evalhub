"""运行固定 10 模型 Coding Mini v3 矩阵并保存本机结果。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evalhub.benchmarks.coding_mini import run_pi_agent_benchmark
from evalhub.model_providers import default_model_provider_repository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / ".runtime/agent-model-matrix-v3"


@dataclass(frozen=True)
class MatrixModel:
    """保存固定矩阵中一次 Agent 运行所需的非敏感模型配置。"""

    slug: str
    model: str
    adapter: str
    provider_id: str | None
    base_url: str


MODELS = (
    MatrixModel(
        "kimi-k2-7-code",
        "moonshotai/Kimi-K2.7-Code",
        "openai-compatible",
        "siliconflow",
        "https://api.siliconflow.cn/v1",
    ),
    MatrixModel(
        "glm-5-2",
        "zai-org/GLM-5.2",
        "openai-compatible",
        "siliconflow",
        "https://api.siliconflow.cn/v1",
    ),
    MatrixModel(
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "openai-compatible",
        "deepseek",
        "https://api.deepseek.com",
    ),
    MatrixModel(
        "deepseek-v4-flash",
        "deepseek-ai/DeepSeek-V4-Flash",
        "openai-compatible",
        "siliconflow",
        "https://api.siliconflow.cn/v1",
    ),
    MatrixModel("qwen3-14b", "qwen3:14b", "ollama", None, "http://127.0.0.1:11434"),
    MatrixModel("gemma4-12b", "gemma4:12b", "ollama", None, "http://127.0.0.1:11434"),
    MatrixModel(
        "granite3-3-8b",
        "granite3.3:8b",
        "ollama",
        None,
        "http://127.0.0.1:11434",
    ),
    MatrixModel("qwen3-4b", "qwen3:4b", "ollama", None, "http://127.0.0.1:11434"),
    MatrixModel(
        "granite4-1-3b",
        "granite4.1:3b",
        "ollama",
        None,
        "http://127.0.0.1:11434",
    ),
    MatrixModel(
        "deepseek-r1-1-5b",
        "deepseek-r1:1.5b",
        "ollama",
        None,
        "http://127.0.0.1:11434",
    ),
)


def run_model(configuration: MatrixModel, *, force: bool = False) -> dict[str, object]:
    """运行一个固定模型并把完整结果写入忽略提交的本机目录。

    参数：
        configuration: 当前模型的公开 ID、适配器和 Provider 配置。
        force: 已存在 v3 结果时是否覆盖重跑。

    返回：
        Coding Mini v3 完整评测结果。
    """
    output_path = OUTPUT_ROOT / f"{configuration.slug}.json"
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if existing.get("benchmark_version") == "coding-mini-v3":
            return existing
    api_key = None

    # 远程凭据只在当前进程内解析并交给受控本机代理，结果文件不会记录真实 Key。
    if configuration.provider_id is not None:
        api_key = default_model_provider_repository().resolve_api_key(configuration.provider_id)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result = run_pi_agent_benchmark(
        job_id=f"matrix-{configuration.slug}-{timestamp}",
        model=configuration.model,
        base_url=configuration.base_url,
        difficulty="all",
        adapter=configuration.adapter,
        provider_id=configuration.provider_id,
        api_key=api_key,
        runtime_root=OUTPUT_ROOT / "runs",
    )

    # 完整样本证据只保存在 .runtime；README 生成阶段只读取聚合后的非敏感字段。
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    """解析单模型参数，运行评测并输出不含凭据的短摘要。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=[model.slug for model in MODELS])
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    configuration = next(model for model in MODELS if model.slug == arguments.model)
    result = run_model(configuration, force=arguments.force)

    # 控制台只打印报告所需聚合字段，避免泄漏失败样本内容或 Provider 凭据。
    summary = {
        "model": result["model"],
        "passed": result["passed_samples"],
        "total": result["total_samples"],
        "protocol": result["protocol_preflight"]["status"],
        "tool_calls": result["execution_summary"]["total_tool_calls"],
        "average_seconds": result["execution_summary"]["average_wall_time_seconds"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
