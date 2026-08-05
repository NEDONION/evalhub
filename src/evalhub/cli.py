"""实现 EvalHub 命令行入口、示例评测、真实 Benchmark 和本地服务分派。"""

import argparse
import json

from evalhub.adapters import (
    ModelAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    StaticMappingAdapter,
)
from evalhub.benchmarks import get_benchmark_spec
from evalhub.datasets import dataset_catalog, get_dataset_spec, load_samples, prepare_dataset
from evalhub.domain import (
    BenchmarkRecord,
    DatasetRecord,
    EvaluationJob,
    EvaluationSample,
    ModelRecord,
    ModelType,
)
from evalhub.engine import EvaluationRunner, ProgressCallback, SampleResultCallback
from evalhub.evaluators import default_evaluator_registry
from evalhub.model_protocols import effective_generation_config
from evalhub.model_providers import (
    ModelProviderRepository,
    default_model_provider_repository,
)
from evalhub.ollama import DEFAULT_OLLAMA_MODEL
from evalhub.registry import InMemoryRegistry


def build_model_adapter(
    adapter_type: str,
    *,
    model: str,
    base_url: str,
    oracle_responses: dict[str, str],
    provider_id: str | None = None,
    provider_repository: ModelProviderRepository | None = None,
) -> ModelAdapter:
    """为文本与 HumanEval 执行路径构造同一模型调用边界。

    Args:
        adapter_type: ``ollama``、``openai-compatible`` 或管线验证用 ``oracle``。
        model: 本地标签或远程服务公开的固定模型 ID。
        base_url: 本地服务地址或任务创建时冻结的远程 API 根地址。
        oracle_responses: Oracle 模式按官方英文提示回放的确定性响应。
        provider_id: 远程 API 模式引用的已保存服务商标识。
        provider_repository: 测试或装配层注入的服务商仓储；缺省使用运行时仓储。

    Returns:
        可供 Runner 调用的统一模型适配器。

    Raises:
        ValueError: 适配器类型不受支持时抛出，禁止静默回退为 Oracle。
    """
    if adapter_type != "openai-compatible" and provider_id is not None:
        raise ValueError("provider_id is only supported by openai-compatible adapter")
    if adapter_type == "ollama":
        return OllamaAdapter(model=model, base_url=base_url)
    if adapter_type == "oracle":
        return StaticMappingAdapter(oracle_responses)
    if adapter_type == "openai-compatible":
        if provider_id is None:
            raise ValueError("provider_id is required for openai-compatible adapter")
        # 任务只保存服务商引用，Worker 在真正发起请求前解析最新轮换后的凭据。
        repository = provider_repository or default_model_provider_repository()
        api_key = repository.resolve_api_key(provider_id)
        return OpenAICompatibleAdapter(
            model=model,
            base_url=base_url,
            api_key=api_key,
            provider_id=provider_id,
        )
    raise ValueError("adapter must be one of: ollama, oracle, openai-compatible")


def run_example() -> int:
    """运行无需外部模型和数据下载的确定性精确匹配示例。

    Returns:
        命令成功完成时返回进程状态码 0。
    """
    # 示例使用独立内存 Registry 和两条固定样本，确保首次运行可快速验证完整管线。
    registry = InMemoryRegistry()
    samples = [
        EvaluationSample(id="sample_1", input="What is 2 + 2?", reference="4"),
        EvaluationSample(
            id="sample_2",
            input="A box has 3 red balls and 2 blue balls. How many balls are there?",
            reference="5",
        ),
    ]

    # 注册模型和数据集记录，使示例与真实执行共享同一套领域关联关系。
    model = registry.models.add(ModelRecord(name="static-demo", version="v1", type=ModelType.API))
    dataset = registry.datasets.add(
        DatasetRecord(
            name="gsm8k-mini",
            version="v1",
            storage_uri="examples/datasets/gsm8k_sample.jsonl",
            schema={"input": "str", "reference": "str"},
            owner="evalhub",
            sample_count=len(samples),
        )
    )
    # Benchmark 绑定数据集与精确匹配指标，并使用确定性温度配置。
    benchmark = registry.benchmarks.add(
        BenchmarkRecord(
            name="gsm8k-mini",
            dataset_id=dataset.id,
            evaluator_type="exact_match",
            config={"temperature": 0},
        )
    )
    job = registry.jobs.add(EvaluationJob(model_id=model.id, benchmark_id=benchmark.id))

    # 静态适配器直接返回参考答案，用于验证执行器、评测器和报告聚合是否贯通。
    adapter = StaticMappingAdapter({sample.input: sample.reference for sample in samples})
    evaluator = default_evaluator_registry().create(benchmark.evaluator_type)
    runner = EvaluationRunner(adapter, evaluator)

    # 执行后把样本结果和任务终态写回 Registry，模拟真实持久化调用顺序。
    results, report = runner.run(job=job, benchmark=benchmark, samples=samples)
    registry.results.add_many(results)
    registry.jobs.update(job)

    # 以 JSON 输出关键报告字段，方便终端阅读和自动化脚本解析。
    print(
        json.dumps(
            {
                "job_id": report.job_id,
                "status": job.status,
                "metric": report.metric,
                "total_samples": report.total_samples,
                "passed_samples": report.passed_samples,
                "average_score": report.average_score,
                "failed_sample_ids": report.failed_sample_ids,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


def list_datasets() -> int:
    """以 JSON 列出当前目录注册的全部公开 Benchmark 数据集。

    Returns:
        列表成功输出时返回进程状态码 0。
    """
    # 输出只包含用户选择与准备数据所需字段，不泄漏内部实现对象。
    rows = []
    for spec in dataset_catalog().values():
        rows.append(
            {
                "name": spec.name,
                "display_name": spec.display_name,
                "task_type": spec.task_type,
                "evaluator_type": spec.evaluator_type,
                "homepage": spec.homepage,
                "local_path": spec.local_path,
            }
        )
    # 保留中文展示名称，缩进输出便于人在终端直接阅读。
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def prepare_dataset_command(dataset: str) -> int:
    """准备指定数据集并输出最终本地路径。

    Args:
        dataset: 数据集目录中注册的稳定名称。

    Returns:
        数据准备成功时返回进程状态码 0。
    """
    # 下载与缓存逻辑由数据集层负责，命令层只转换结果为用户可读 JSON。
    path = prepare_dataset(dataset)
    print(json.dumps({"dataset": dataset, "path": str(path)}, ensure_ascii=False, indent=2))
    return 0


def run_real_benchmark(
    *,
    dataset: str,
    adapter_type: str,
    model: str,
    base_url: str,
    limit: int | None,
    subject: str,
    job_id: str | None = None,
    on_progress: ProgressCallback | None = None,
    skip_sample_ids: set[str] | frozenset[str] = frozenset(),
    on_sample_result: SampleResultCallback | None = None,
    generation_config: dict[str, object] | None = None,
    evaluator_type: str | None = None,
    provider_id: str | None = None,
) -> dict[str, object]:
    """准备真实数据集并使用指定模型适配器执行同步评测。

    Args:
        dataset: 数据集目录中的稳定名称。
        adapter_type: ``ollama``、``openai-compatible`` 或管线校验用 ``oracle``。
        model: 记录和模型适配器使用的模型标签。
        base_url: Ollama 本地服务根地址。
        limit: 最多执行的样本数；为 ``None`` 时执行完整数据集。
        subject: MMLU 学科名称，其他数据集会忽略该值。
        job_id: 调度层预先创建的任务标识；为空时沿用领域实体默认生成逻辑。
        on_progress: 接收已完成样本数与总样本数的可选进度回调。
        skip_sample_ids: 恢复执行时已经完成推理和评分的样本标识。
        on_sample_result: 新样本完成评分后接收完整结果的可选回调。
        generation_config: 工作流创建时冻结的模型生成参数；缺省保持历史确定性配置。
        evaluator_type: 工作流创建时冻结的评分器类型；缺省使用当前数据集目录值。
        provider_id: 远程 API 模式引用的服务商标识；其他模式必须为空。

    Returns:
        包含任务状态、汇总指标和最多五条失败示例的 JSON 兼容字典。

    Raises:
        RuntimeError: 数据集没有加载到任何有效样本。
        ValueError: 模型适配器类型不受支持。
    """
    # 规格提供评测器类型和本地路径；准备步骤保证随后加载时缓存已经存在。
    spec = get_dataset_spec(dataset)
    prepare_dataset(dataset)
    # 只有 MMLU 使用学科筛选，其他数据集显式传入 ``None`` 避免无效参数生效。
    samples = load_samples(
        dataset,
        limit=limit,
        subject=subject if dataset == "mmlu" else None,
    )
    if not samples:
        raise RuntimeError(f"no samples loaded for dataset: {dataset}")
    # 样本加载完成后先公布真实分母，让任务在首条模型响应前也能显示确定进度。
    completed_before_run = sum(1 for sample in samples if sample.id in skip_sample_ids)
    if on_progress is not None:
        on_progress(completed_before_run, len(samples))

    # 每次命令创建独立 Registry，避免本地重复试跑共享上一轮的临时状态。
    registry = InMemoryRegistry()
    model_record = registry.models.add(ModelRecord(name=model, version="local", type=ModelType.API))
    # 注册数据集快照记录，为任务报告保留样本规模、来源路径与公开归属。
    dataset_record = registry.datasets.add(
        DatasetRecord(
            name=spec.name,
            version="test",
            storage_uri=spec.local_path,
            schema={"input": "str", "reference": "str", "metadata": "json"},
            owner="public",
            sample_count=len(samples),
        )
    )
    # 直接 CLI 运行同样读取 Registry 的分项预算；自定义数据集保留历史 256 token 默认值。
    if generation_config is None:
        try:
            default_config = dict(get_benchmark_spec(dataset).generation_config)
        except KeyError:
            default_config = {"temperature": 0, "num_predict": 256}
        effective_config = (
            effective_generation_config(model, default_config)
            if adapter_type == "ollama"
            else default_config
        )
    else:
        effective_config = dict(generation_config)
    # Benchmark 从目录选择匹配的评测器，并保存已经合并完成的确定性生成选项。
    benchmark = registry.benchmarks.add(
        BenchmarkRecord(
            name=spec.display_name,
            dataset_id=dataset_record.id,
            evaluator_type=spec.evaluator_type,
            config=effective_config,
        )
    )
    # 调度层提供任务标识时沿用同一 ID，CLI 直接运行则继续使用领域默认值。
    if job_id is None:
        evaluation_job = EvaluationJob(model_id=model_record.id, benchmark_id=benchmark.id)
    else:
        evaluation_job = EvaluationJob(
            id=job_id,
            model_id=model_record.id,
            benchmark_id=benchmark.id,
        )
    job = registry.jobs.add(evaluation_job)

    # 两条模型执行路径共享同一构造边界，Oracle 只回放官方英文输入对应的参考答案。
    adapter = build_model_adapter(
        adapter_type,
        model=model,
        base_url=base_url,
        oracle_responses={sample.input: sample.reference for sample in samples},
        provider_id=provider_id,
    )

    # 评测器由 Benchmark 类型动态创建，Runner 只负责统一编排与状态转换。
    evaluator = default_evaluator_registry().create(evaluator_type or benchmark.evaluator_type)
    runner = EvaluationRunner(adapter, evaluator)
    results, report = runner.run(
        job=job,
        benchmark=benchmark,
        samples=samples,
        on_progress=on_progress,
        skip_sample_ids=skip_sample_ids,
        on_sample_result=on_sample_result,
    )

    # 报告只携带前五条失败示例并截断长文本，控制 CLI 与 HTTP 响应体积。
    failed_examples = [
        {
            "sample_id": result.sample_id,
            "score": result.score,
            "input": result.input[:800],
            "prediction": result.prediction[:800],
            "reference": result.reference,
            "reason": result.reason,
            "metadata": result.metadata,
        }
        for result in results
        if result.score < 1.0
    ][:5]

    # 返回值保持 JSON 兼容，供 CLI 输出和本地 HTTP 服务复用同一业务入口。
    return {
        "job_id": report.job_id,
        "status": job.status,
        "dataset": dataset,
        "benchmark": benchmark.name,
        "model": model,
        "adapter": adapter_type,
        "metric": report.metric,
        "total_samples": report.total_samples,
        "passed_samples": report.passed_samples,
        "average_score": round(report.average_score, 4),
        "failed_sample_ids": report.failed_sample_ids,
        "failed_examples": failed_examples,
    }


def run_benchmark_command(args: argparse.Namespace) -> int:
    """把 argparse 参数转换为真实 Benchmark 调用并输出 JSON。

    Returns:
        评测成功并完成输出时返回进程状态码 0。
    """
    # 显式映射参数字段，避免 Namespace 中新增选项被无意传入业务函数。
    result = run_real_benchmark(
        dataset=args.dataset,
        adapter_type=args.adapter,
        model=args.model,
        base_url=args.base_url,
        limit=args.limit,
        subject=args.subject,
        provider_id=args.provider_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def serve_command(host: str, port: int) -> int:
    """延迟导入并启动本地前后端一体化 HTTP 服务。

    Args:
        host: HTTP 服务器监听地址。
        port: HTTP 服务器监听端口。

    Returns:
        服务正常停止后返回进程状态码 0。
    """
    # 延迟导入避免纯 CLI 命令承担 HTTP 服务模块的加载成本和副作用。
    from evalhub.server import serve

    serve(host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建包含示例、数据集、评测和服务命令的参数解析器。

    Returns:
        已注册全部子命令、选项、默认值和约束的解析器。
    """
    # 必选子命令防止空调用静默成功，并让 argparse 自动生成统一帮助信息。
    parser = argparse.ArgumentParser(prog="evalhub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-example", help="Run a deterministic exact-match evaluation.")

    # 列表命令没有额外参数，直接展示目录中当前支持的数据集。
    subparsers.add_parser("list-datasets", help="List supported real benchmark datasets.")

    # 准备命令使用目录键作为 choices，在解析阶段阻止未知数据集进入下载逻辑。
    prepare_parser = subparsers.add_parser(
        "prepare-dataset",
        help="Download and cache a real public benchmark dataset locally.",
    )
    prepare_parser.add_argument("dataset", choices=sorted(dataset_catalog()))

    # 评测命令集中声明数据、适配器、模型连接和采样范围，默认值支持最小本地试跑。
    run_parser = subparsers.add_parser(
        "run-benchmark",
        help="Run a local evaluation on a real public benchmark dataset.",
    )
    run_parser.add_argument("--dataset", choices=sorted(dataset_catalog()), default="gsm8k")
    run_parser.add_argument(
        "--adapter",
        choices=["ollama", "oracle", "openai-compatible"],
        default="ollama",
    )
    run_parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    run_parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    run_parser.add_argument("--provider-id", default=None)
    # 数量与学科选项控制数据加载范围，不改变数据集和模型身份参数。
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--subject", default="all")

    # 服务命令默认只监听本机回环地址，避免开发控制台意外暴露到外部网络。
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the local frontend and backend in one process.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    return parser


def main() -> int:
    """解析命令行并把请求分派给对应命令处理函数。

    Returns:
        被选中命令返回的进程状态码。

    Raises:
        ValueError: 解析结果包含未实现的命令名称。
    """
    # 所有命令共享同一解析入口，子命令处理函数只接收完成验证的参数。
    args = build_parser().parse_args()
    if args.command == "run-example":
        return run_example()
    if args.command == "list-datasets":
        return list_datasets()
    # 数据准备是独立命令，必须在进入带推理副作用的评测分派之前处理。
    if args.command == "prepare-dataset":
        return prepare_dataset_command(args.dataset)
    # 带复杂参数的评测和服务命令分别传递完整 Namespace 或明确监听配置。
    if args.command == "run-benchmark":
        return run_benchmark_command(args)
    if args.command == "serve":
        return serve_command(args.host, args.port)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    # 把命令返回码交给解释器，使 shell 和自动化任务能准确识别执行状态。
    raise SystemExit(main())
