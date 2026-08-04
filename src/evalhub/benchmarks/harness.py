"""连接官方 lm-eval 任务与 EvalHub 持久化运行时。"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from evalhub.benchmarks.models import BenchmarkSpec, ExecutorKind

DOCKER_IMAGE = "evalhub-lm-eval:0.4.12"
MODEL_TOKENIZERS = {
    "qwen2.5:0.5b": "Qwen/Qwen2.5-0.5B-Instruct",
    "qwen2.5:1.5b": "Qwen/Qwen2.5-1.5B-Instruct",
    "llama3.2:1b": "meta-llama/Llama-3.2-1B-Instruct",
    "llama3.2:3b": "meta-llama/Llama-3.2-3B-Instruct",
    "deepseek-r1:1.5b": "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
    "phi3:mini": "microsoft/Phi-3-mini-4k-instruct",
}
_CHAT_COMPLETION_BENCHMARKS = frozenset({"mmlu-pro", "ifeval", "math-500", "bbh"})
_PROMPT_LOGPROB_BENCHMARKS = frozenset(
    {"arc-challenge", "musr", "hellaswag", "truthfulqa", "bbq"}
)

_DOCKER_RUN_SCRIPT = """
import json
import os
import lm_eval

limit = os.environ.get("EVALHUB_LIMIT")
result = lm_eval.simple_evaluate(
    model="local-chat-completions",
    model_args=os.environ["EVALHUB_MODEL_ARGS"],
    tasks=[os.environ["EVALHUB_TASK"]],
    limit=int(limit) if limit else None,
    log_samples=True,
    apply_chat_template=True,
    confirm_run_unsafe_code=True,
)
with open("/output/result.json", "w", encoding="utf-8") as file:
    json.dump(result, file, ensure_ascii=False, default=str)
""".strip()

_PREPARE_TASK_SCRIPT = """
import sys
from lm_eval.tasks import TaskManager

TaskManager().load([sys.argv[1]])
""".strip()


def _simple_evaluate(**kwargs: object) -> dict[str, object]:
    """延迟调用官方 Harness Python API。

    Args:
        **kwargs: 原样传给 ``lm_eval.simple_evaluate`` 的稳定评测参数。

    Returns:
        Harness 生成的 JSON 兼容结果映射。

    Raises:
        RuntimeError: Harness 未返回结果或返回了不支持的结构。
    """
    import lm_eval

    result = lm_eval.simple_evaluate(**kwargs)
    if not isinstance(result, Mapping):
        raise RuntimeError("lm-eval 未返回可解析的评测结果")
    return dict(result)


def _lm_eval_installed() -> bool:
    """返回官方 Harness 依赖是否可导入。

    Returns:
        当前 Python 环境能找到 ``lm_eval`` 时返回 ``True``。
    """
    packages = ("lm_eval", "transformers")
    return all(importlib.util.find_spec(package) is not None for package in packages)


def _docker_ready() -> bool:
    """返回代码评测 Docker 镜像是否可用。

    Returns:
        Docker CLI、Daemon 和固定 EvalHub 镜像都可访问时返回 ``True``。
    """
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "image", "inspect", DOCKER_IMAGE],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def benchmark_readiness(spec: BenchmarkSpec) -> tuple[bool, str | None]:
    """返回 Benchmark 本地执行边界是否就绪。

    Args:
        spec: 需要检查的 Registry Benchmark 规格。

    Returns:
        就绪布尔值，以及未就绪时可直接展示的中文原因。
    """
    if spec.executor == ExecutorKind.NATIVE:
        return True, None
    if not _lm_eval_installed():
        return False, "lm-eval 评测依赖尚未安装"
    if spec.id in _PROMPT_LOGPROB_BENCHMARKS:
        return False, "Ollama 当前不提供官方多选协议所需的 prompt logprobs"
    # 只有生成代码的任务需要额外的宿主隔离运行时。
    if spec.executor == ExecutorKind.SANDBOXED_CODE and not _docker_ready():
        return False, "Docker 评测镜像尚未就绪"
    return True, None


def prepare_harness_benchmark(
    benchmark_id: str, *, root: Path | None = None, force: bool = False
) -> Path:
    """加载官方任务和数据集并写入本地资产标记。

    Args:
        benchmark_id: Registry 中的外部 Benchmark 稳定标识。
        root: `.runtime` 缓存所在的项目根目录；省略时使用当前目录。
        force: 已有标记时是否仍重新验证官方任务和数据资产。

    Returns:
        成功验证后写入的 JSON 资产标记路径。

    Raises:
        ValueError: 误把原生 Benchmark 传给外部准备入口。
        RuntimeError: 依赖或 Docker 未就绪，或官方任务验证失败。
    """
    from evalhub.benchmarks.registry import get_benchmark_spec

    root = root or Path.cwd()
    spec = get_benchmark_spec(benchmark_id)
    if spec.executor == ExecutorKind.NATIVE:
        raise ValueError(f"native benchmark does not use lm-eval: {benchmark_id}")
    if spec.id in _PROMPT_LOGPROB_BENCHMARKS:
        raise ValueError(
            f"ollama_prompt_logprobs_unsupported: {spec.display_name} 官方协议需要 "
            "prompt logprobs"
        )
    ready, reason = benchmark_readiness(spec)
    if not ready:
        raise RuntimeError(reason or f"{spec.display_name} 执行器未就绪")

    marker = root / ".runtime/benchmarks" / f"{spec.id}.json"
    if marker.is_file() and not force:
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("preparation") == "task_data_loaded":
            return marker
    cache = root / ".runtime/huggingface"
    cache.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    # TaskManager 构造任务时会真实加载数据集，但不会调用模型或产生虚假分数。
    environment = {**os.environ, "HF_HOME": str(cache.resolve())}
    command = [sys.executable, "-c", _PREPARE_TASK_SCRIPT, spec.task_name]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"lm-eval task validation failed: {message}") from exc
    # 标记只保存可审计协议元数据；真实数据和 tokenizer 保持在 Hugging Face 缓存中。
    marker.write_text(
        json.dumps(
            {
                "benchmark_id": spec.id,
                "task_name": spec.task_name,
                "dataset_source": spec.dataset_source,
                "dataset_revision": spec.dataset_revision,
                "executor": spec.executor.value,
                "preparation": "task_data_loaded",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return marker


def _run_code_benchmark(
    spec: BenchmarkSpec,
    *,
    model: str,
    base_url: str,
    limit: int | None,
) -> dict[str, object]:
    """在受限 Docker 容器运行代码 Benchmark 并读取 JSON 结果。

    Args:
        spec: HumanEval 或 MBPP 的 Registry 规格。
        model: Ollama 模型标签。
        base_url: 宿主 Ollama 服务根地址。
        limit: 可选样本上限；``None`` 表示完整数据集。

    Returns:
        容器内 ``simple_evaluate`` 写出的原始 Harness 结果。

    Raises:
        RuntimeError: 容器成功退出但没有生成可解析结果文件。
        subprocess.CalledProcessError: Docker 或容器评测进程失败。
    """
    cache = Path(".runtime/huggingface")
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="evalhub-code-") as temporary:
        output = Path(temporary)
        command = build_docker_command(
            spec,
            model=model,
            base_url=base_url,
            limit=limit,
            output_dir=output,
            cache_dir=cache,
        )
        # 参数列表不经过 shell；生成代码只在只读、限额容器内部由 Harness 执行。
        subprocess.run(command, check=True, capture_output=True, text=True)
        result_path = output / "result.json"
        if not result_path.is_file():
            raise RuntimeError("Docker code benchmark did not produce result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("Docker code benchmark returned invalid JSON")
    return result


def run_harness_benchmark(
    benchmark_id: str,
    *,
    model: str,
    base_url: str,
    limit: int | None,
    on_progress: Callable[[int, int], None],
    on_sample_result: Callable[[dict[str, object], int, int], None],
) -> dict[str, object]:
    """执行一个外部 Harness Benchmark 并转换结果。

    Args:
        benchmark_id: Registry 中的 Benchmark 稳定标识。
        model: Ollama 模型标签。
        base_url: Ollama 服务根地址。
        limit: 最大样本数；``None`` 表示完整官方数据集。
        on_progress: 报告当前 Benchmark 样本进度的回调。
        on_sample_result: 把 Harness 样本写入 SQLite 的回调。

    Returns:
        与原生 Benchmark 输出兼容的结果摘要。

    Raises:
        ValueError: Benchmark 使用原生执行器或模型缺少 tokenizer 映射。
        RuntimeError: Docker 或 Harness 没有返回有效结果。
    """
    from evalhub.benchmarks.registry import get_benchmark_spec

    spec = get_benchmark_spec(benchmark_id)
    if spec.executor == ExecutorKind.NATIVE:
        raise ValueError(f"native benchmark does not use lm-eval: {benchmark_id}")
    if spec.id in _PROMPT_LOGPROB_BENCHMARKS:
        raise ValueError(
            f"ollama_prompt_logprobs_unsupported: {spec.display_name} 官方协议需要 "
            "prompt logprobs"
        )
    cache = Path(".runtime/huggingface").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache)
    on_progress(0, 0)
    if spec.executor == ExecutorKind.SANDBOXED_CODE:
        raw = _run_code_benchmark(
            spec,
            model=model,
            base_url=base_url,
            limit=limit,
        )
    elif spec.id in _CHAT_COMPLETION_BENCHMARKS:
        raw = _simple_evaluate(
            model="local-chat-completions",
            model_args=_chat_model_args(
                model,
                f"{base_url.rstrip('/')}/v1/chat/completions",
            ),
            tasks=[spec.task_name],
            limit=limit,
            batch_size=1,
            log_samples=True,
            apply_chat_template=True,
        )
    else:
        tokenizer = tokenizer_for_model(model)
        raw = _simple_evaluate(
            model="local-completions",
            model_args=_model_args(
                model,
                f"{base_url.rstrip('/')}/v1/completions",
                tokenizer,
            ),
            tasks=[spec.task_name],
            limit=limit,
            batch_size=1,
            log_samples=True,
            apply_chat_template=True,
        )
    result = convert_harness_result(
        spec,
        raw,
        model=model,
        on_sample_result=on_sample_result,
    )
    on_progress(int(result["total_samples"]), int(result["total_samples"]))
    return result


def tokenizer_for_model(model: str) -> str:
    """返回 Ollama 模型对应的 Hugging Face tokenizer。

    Args:
        model: Ollama 使用的完整模型标签。

    Returns:
        可由 ``transformers`` 下载和加载的 tokenizer 仓库标识。

    Raises:
        ValueError: 模型没有经过显式映射，无法保证多选题 token 对齐。
    """
    try:
        return MODEL_TOKENIZERS[model]
    except KeyError as exc:
        raise ValueError(f"tokenizer_not_configured: 未配置模型 {model} 的 tokenizer") from exc


def _metric_value(values: object, metric: str) -> float | None:
    """从 Harness 指标映射读取忽略 filter 后缀的数值。

    Args:
        values: Harness 返回的未知 JSON 值。
        metric: Registry 声明的基础指标名称。

    Returns:
        匹配指标的浮点值；结构或值不合法时返回 ``None``。
    """
    if not isinstance(values, dict):
        return None
    # Harness 使用 ``acc,none`` 等键携带过滤器，Registry 只保存稳定基础名称。
    for key, value in values.items():
        if str(key).split(",", 1)[0] != metric:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _sample_prediction(sample: dict[str, object]) -> object:
    """提取 Harness 过滤后的首个模型响应供调试页面展示。

    Args:
        sample: 一条 ``log_samples`` 记录。

    Returns:
        首个过滤响应；响应缺失时返回空字符串。
    """
    responses = sample.get("filtered_resps", sample.get("resps", []))
    if isinstance(responses, list) and responses:
        return responses[0]
    return ""


def convert_harness_result(
    spec: BenchmarkSpec,
    raw: dict[str, object],
    *,
    model: str,
    on_sample_result: Callable[[dict[str, object], int, int], None],
) -> dict[str, object]:
    """把 lm-eval 原始结果转换成 EvalHub Benchmark 输出。

    Args:
        spec: 当前 Registry Benchmark 规格。
        raw: ``simple_evaluate`` 返回的 JSON 兼容结果。
        model: 本次评测使用的 Ollama 模型标签。
        on_sample_result: 依次持久化样本结果和节点进度的回调。

    Returns:
        可直接交给能力聚合节点的统一 Benchmark 摘要。
    """
    sample_groups = raw.get("samples", {})
    groups = sample_groups if isinstance(sample_groups, dict) else {}
    flat_samples: list[tuple[str, dict[str, object]]] = []
    # 保持 Harness 返回的任务和样本顺序，使重复运行得到稳定检查点标识。
    for task_name, values in groups.items():
        if isinstance(values, list):
            flat_samples.extend(
                (str(task_name), value) for value in values if isinstance(value, dict)
            )

    scores: list[float] = []
    total = len(flat_samples)
    for completed, (task_name, sample) in enumerate(flat_samples, start=1):
        score = _metric_value(sample, spec.metric) or 0.0
        scores.append(score)
        # 组任务的 doc_id 只在子任务内唯一，因此稳定标识必须带子任务名称。
        sample_id = f"{task_name}:{sample.get('doc_id', completed - 1)}"
        on_sample_result(
            {
                "sample_id": sample_id,
                "input": sample.get("doc", ""),
                "prediction": _sample_prediction(sample),
                "reference": sample.get("target", ""),
                "metric": spec.metric,
                "score": score,
                "reason": None,
            },
            completed,
            total,
        )

    result_groups = raw.get("results", {})
    result_rows = result_groups if isinstance(result_groups, dict) else {}
    raw_score = _metric_value(result_rows.get(spec.task_name), spec.metric)
    if raw_score is None:
        raw_score = sum(scores) / len(scores) if scores else 0.0
    # 外部 Harness 的总分保留官方聚合结果，样本统计继续匹配现有任务详情契约。
    return {
        "benchmark_id": spec.id,
        "benchmark": spec.display_name,
        "status": "success",
        "model": model,
        "metric": spec.metric,
        "dataset_source": spec.dataset_source,
        "dataset_revision": spec.dataset_revision,
        "raw_score": round(raw_score, 6),
        "score_sum": round(sum(scores), 6),
        "total_samples": total,
        "passed_samples": sum(score >= 1.0 for score in scores),
        "failed_sample_ids": [
            f"{task_name}:{sample.get('doc_id', index)}"
            for index, ((task_name, sample), score) in enumerate(
                zip(flat_samples, scores, strict=True)
            )
            if score < 1.0
        ],
        "protocol_scope": "lm_eval_0.4.12",
    }


def _container_base_url(base_url: str) -> str:
    """把宿主回环 Ollama 地址转换为 Docker Desktop 可访问地址。

    Args:
        base_url: 用户在控制台配置的 Ollama 服务根地址。

    Returns:
        指向 OpenAI Chat Completions 端点的容器内 URL。
    """
    root = base_url.rstrip("/")
    root = root.replace("http://127.0.0.1", "http://host.docker.internal", 1)
    root = root.replace("http://localhost", "http://host.docker.internal", 1)
    return f"{root}/v1/chat/completions"


def _model_args(model: str, base_url: str, tokenizer: str) -> str:
    """生成 ``local-completions`` 使用的确定性模型参数字符串。

    Args:
        model: Ollama 模型标签。
        base_url: 已包含 `/v1/completions` 的端点。
        tokenizer: Hugging Face tokenizer 标识。

    Returns:
        可直接传给 lm-eval ``model_args`` 的逗号分隔参数。
    """
    return ",".join(
        (
            f"model={model}",
            f"base_url={base_url}",
            f"tokenizer={tokenizer}",
            "tokenized_requests=False",
            "tokenizer_backend=huggingface",
            "num_concurrent=1",
            "max_retries=3",
            "timeout=120",
        )
    )


def _chat_model_args(model: str, base_url: str) -> str:
    """生成 Ollama Chat Completions 使用的确定性模型参数。"""
    return ",".join(
        (
            f"model={model}",
            f"base_url={base_url}",
            "num_concurrent=1",
            "max_retries=3",
            "timeout=120",
        )
    )


def build_docker_command(
    spec: BenchmarkSpec,
    *,
    model: str,
    base_url: str,
    limit: int | None,
    output_dir: Path,
    cache_dir: Path,
) -> list[str]:
    """构造在隔离容器运行代码 Benchmark 的参数列表。

    Args:
        spec: HumanEval 或 MBPP 的 Registry 规格。
        model: Ollama 模型标签。
        base_url: 宿主 Ollama 服务根地址。
        limit: 可选的调试样本上限；``None`` 表示完整数据集。
        output_dir: 仅用于写入本次 JSON 结果的宿主目录。
        cache_dir: 可复用的 Hugging Face 数据和 tokenizer 缓存。

    Returns:
        不经 shell 解释、可直接交给 ``subprocess.run`` 的参数列表。
    """
    model_args = _chat_model_args(model, _container_base_url(base_url))
    command = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,size=512m",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--pids-limit",
        "128",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-v",
        f"{output_dir.resolve()}:/output",
        "-v",
        f"{cache_dir.resolve()}:/root/.cache/huggingface",
        "-e",
        f"EVALHUB_TASK={spec.task_name}",
        "-e",
        f"EVALHUB_MODEL_ARGS={model_args}",
    ]
    # 空环境变量会在容器脚本中还原为全量运行，避免 Docker 参数出现 Python None。
    command.extend(["-e", f"EVALHUB_LIMIT={limit or ''}"])
    command.extend([DOCKER_IMAGE, "python", "-c", _DOCKER_RUN_SCRIPT])
    return command
