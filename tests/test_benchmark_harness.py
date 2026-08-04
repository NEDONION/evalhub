"""验证官方 lm-eval 适配与代码评测 Docker 隔离边界。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import evalhub.benchmarks.harness as harness_module
from evalhub.benchmarks.harness import (
    DOCKER_IMAGE,
    benchmark_readiness,
    build_docker_command,
    convert_harness_result,
    prepare_harness_benchmark,
    run_harness_benchmark,
    tokenizer_for_model,
)
from evalhub.benchmarks.models import ExecutorKind
from evalhub.benchmarks.registry import get_benchmark_spec


@pytest.mark.parametrize(
    ("model", "tokenizer"),
    [
        ("qwen2.5:0.5b", "Qwen/Qwen2.5-0.5B-Instruct"),
        ("qwen2.5:1.5b", "Qwen/Qwen2.5-1.5B-Instruct"),
        ("llama3.2:1b", "meta-llama/Llama-3.2-1B-Instruct"),
        ("deepseek-r1:1.5b", "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"),
        ("phi3:mini", "microsoft/Phi-3-mini-4k-instruct"),
    ],
)
def test_recommended_ollama_models_have_huggingface_tokenizers(
    model: str, tokenizer: str
) -> None:
    """外部多选任务需要把推荐 Ollama 标签映射到可下载 tokenizer。"""
    assert tokenizer_for_model(model) == tokenizer


def test_unknown_model_reports_actionable_tokenizer_error() -> None:
    """自定义模型不能静默退回错误 tokenizer 并产生不可比较分数。"""
    with pytest.raises(ValueError, match="tokenizer_not_configured"):
        tokenizer_for_model("private-model:latest")


def test_harness_result_converts_group_samples_and_metric() -> None:
    """Harness 子任务样本应转换为现有 SQLite 回调和统一 Benchmark 摘要。"""
    spec = get_benchmark_spec("mmlu-pro")
    raw = {
        "results": {"mmlu_pro": {"exact_match,custom-extract": 0.75}},
        "samples": {
            "mmlu_pro_biology": [
                {
                    "doc_id": 7,
                    "doc": {"question": "Cell?"},
                    "target": "A",
                    "filtered_resps": ["A"],
                    "exact_match": 1.0,
                }
            ],
            "mmlu_pro_math": [
                {
                    "doc_id": 2,
                    "doc": {"question": "1+1?"},
                    "target": "B",
                    "filtered_resps": ["C"],
                    "exact_match": 0.0,
                }
            ],
        },
    }
    observed: list[tuple[dict[str, object], int, int]] = []

    result = convert_harness_result(
        spec,
        raw,
        model="qwen2.5:0.5b",
        on_sample_result=lambda sample, completed, total: observed.append(
            (sample, completed, total)
        ),
    )

    assert result["raw_score"] == 0.75
    assert result["total_samples"] == 2
    assert result["passed_samples"] == 1
    assert [item[0]["sample_id"] for item in observed] == [
        "mmlu_pro_biology:7",
        "mmlu_pro_math:2",
    ]
    assert observed[-1][1:] == (2, 2)
    assert observed[0][0]["input"] == {"question": "Cell?"}


def test_code_benchmark_command_applies_container_limits(tmp_path: Path) -> None:
    """HumanEval/MBPP 必须在只读、限额且无特权的 Docker 容器运行。"""
    output = tmp_path / "output"
    cache = tmp_path / "cache"
    output.mkdir()
    cache.mkdir()

    command = build_docker_command(
        get_benchmark_spec("humaneval"),
        model="qwen2.5:0.5b",
        base_url="http://127.0.0.1:11434",
        tokenizer="Qwen/Qwen2.5-0.5B-Instruct",
        limit=None,
        output_dir=output,
        cache_dir=cache,
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[command.index("--cap-drop") :][:2]
    assert "no-new-privileges" in command
    assert "--pids-limit" in command
    assert "host.docker.internal:11434" in " ".join(command)
    assert DOCKER_IMAGE in command
    assert "--confirm-run-unsafe-code" not in command


def test_lm_eval_run_uses_ollama_completion_endpoint_and_full_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部任务应使用官方 Python API，并保持全量评测的 ``None`` 上限。"""
    observed: dict[str, object] = {}

    def fake_evaluate(**kwargs: object) -> dict[str, object]:
        """记录传给官方 Harness 的参数并返回一条成功样本。"""
        observed.update(kwargs)
        return {
            "results": {"arc_challenge": {"acc_norm,none": 1.0}},
            "samples": {
                "arc_challenge": [
                    {
                        "doc_id": 0,
                        "doc": {"question": "Q"},
                        "target": "A",
                        "filtered_resps": ["A"],
                        "acc_norm": 1.0,
                    }
                ]
            },
        }

    monkeypatch.setattr(harness_module, "_simple_evaluate", fake_evaluate)
    samples: list[dict[str, object]] = []
    progress: list[tuple[int, int]] = []

    result = run_harness_benchmark(
        "arc-challenge",
        model="qwen2.5:0.5b",
        base_url="http://127.0.0.1:11434",
        limit=None,
        on_progress=lambda completed, total: progress.append((completed, total)),
        on_sample_result=lambda sample, completed, total: samples.append(sample),
    )

    assert observed["model"] == "local-completions"
    assert "base_url=http://127.0.0.1:11434/v1/completions" in str(
        observed["model_args"]
    )
    assert observed["tasks"] == ["arc_challenge"]
    assert observed["limit"] is None
    assert observed["log_samples"] is True
    assert result["raw_score"] == 1.0
    assert len(samples) == 1
    assert progress[-1] == (1, 1)


def test_prepare_external_benchmark_validates_task_and_writes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """资产准备应通过官方 validate 加载任务，并留下 UI 可读取的本地标记。"""
    run = Mock()
    monkeypatch.setattr(harness_module, "_lm_eval_installed", lambda: True)
    monkeypatch.setattr(harness_module.subprocess, "run", run)

    marker = prepare_harness_benchmark("ifeval", root=tmp_path)

    command = run.call_args.args[0]
    assert command[-2:] == ["--tasks", "ifeval"]
    assert run.call_args.kwargs["check"] is True
    assert marker == tmp_path / ".runtime/benchmarks/ifeval.json"
    assert marker.is_file()
    assert '"task_name": "ifeval"' in marker.read_text(encoding="utf-8")


def test_readiness_checks_dependency_and_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry 可运行状态应区分原生、缺少 Harness 与缺少 Docker 镜像。"""
    native = get_benchmark_spec("gsm8k")
    external = get_benchmark_spec("ifeval")
    code = get_benchmark_spec("humaneval")
    assert native.executor == ExecutorKind.NATIVE
    assert benchmark_readiness(native) == (True, None)

    monkeypatch.setattr(harness_module, "_lm_eval_installed", lambda: False)
    assert benchmark_readiness(external) == (False, "lm-eval 评测依赖尚未安装")

    monkeypatch.setattr(harness_module, "_lm_eval_installed", lambda: True)
    monkeypatch.setattr(harness_module, "_docker_ready", lambda: False)
    assert benchmark_readiness(code) == (False, "Docker 评测镜像尚未就绪")


def test_code_benchmark_reads_docker_result_without_host_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """代码任务只应消费容器 JSON 结果，并继续发送现有样本检查点。"""
    monkeypatch.chdir(tmp_path)
    observed_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> None:
        """模拟 Docker 成功并向只挂载的输出目录写入 Harness 结果。"""
        observed_command.extend(command)
        output_mount = next(
            item for item in command if isinstance(item, str) and item.endswith(":/output")
        )
        output_dir = Path(output_mount.removesuffix(":/output"))
        (output_dir / "result.json").write_text(
            json.dumps(
                {
                    "results": {"humaneval": {"pass@1,none": 1.0}},
                    "samples": {
                        "humaneval": [
                            {
                                "doc_id": 0,
                                "doc": {"prompt": "def add(a, b):"},
                                "target": "tests",
                                "filtered_resps": ["return a + b"],
                                "pass@1": 1.0,
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(harness_module.subprocess, "run", fake_run)
    samples: list[dict[str, object]] = []

    result = run_harness_benchmark(
        "humaneval",
        model="qwen2.5:0.5b",
        base_url="http://127.0.0.1:11434",
        limit=1,
        on_progress=lambda completed, total: None,
        on_sample_result=lambda sample, completed, total: samples.append(sample),
    )

    assert observed_command[:3] == ["docker", "run", "--rm"]
    assert result["total_samples"] == 1
    assert result["passed_samples"] == 1
    assert samples[0]["prediction"] == "return a + b"
