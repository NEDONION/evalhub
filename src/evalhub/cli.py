import argparse
import json

from evalhub.adapters import OllamaAdapter, StaticMappingAdapter
from evalhub.datasets import dataset_catalog, get_dataset_spec, load_samples, prepare_dataset
from evalhub.domain import (
    BenchmarkRecord,
    DatasetRecord,
    EvaluationJob,
    EvaluationSample,
    ModelRecord,
    ModelType,
)
from evalhub.engine import EvaluationRunner
from evalhub.evaluators import default_evaluator_registry
from evalhub.registry import InMemoryRegistry


def run_example() -> int:
    registry = InMemoryRegistry()
    samples = [
        EvaluationSample(id="sample_1", input="What is 2 + 2?", reference="4"),
        EvaluationSample(
            id="sample_2",
            input="A box has 3 red balls and 2 blue balls. How many balls are there?",
            reference="5",
        ),
    ]

    model = registry.models.add(
        ModelRecord(name="static-demo", version="v1", type=ModelType.API)
    )
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
    benchmark = registry.benchmarks.add(
        BenchmarkRecord(
            name="gsm8k-mini",
            dataset_id=dataset.id,
            evaluator_type="exact_match",
            config={"temperature": 0},
        )
    )
    job = registry.jobs.add(EvaluationJob(model_id=model.id, benchmark_id=benchmark.id))

    adapter = StaticMappingAdapter({sample.input: sample.reference for sample in samples})
    evaluator = default_evaluator_registry().create(benchmark.evaluator_type)
    runner = EvaluationRunner(adapter, evaluator)

    results, report = runner.run(job=job, benchmark=benchmark, samples=samples)
    registry.results.add_many(results)
    registry.jobs.update(job)

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
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def prepare_dataset_command(dataset: str) -> int:
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
) -> dict[str, object]:
    spec = get_dataset_spec(dataset)
    prepare_dataset(dataset)
    samples = load_samples(
        dataset,
        limit=limit,
        subject=subject if dataset == "mmlu" else None,
    )
    if not samples:
        raise RuntimeError(f"no samples loaded for dataset: {dataset}")

    registry = InMemoryRegistry()
    model_record = registry.models.add(ModelRecord(name=model, version="local", type=ModelType.API))
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
    benchmark = registry.benchmarks.add(
        BenchmarkRecord(
            name=spec.display_name,
            dataset_id=dataset_record.id,
            evaluator_type=spec.evaluator_type,
            config={"temperature": 0, "num_predict": 256},
        )
    )
    job = registry.jobs.add(EvaluationJob(model_id=model_record.id, benchmark_id=benchmark.id))

    if adapter_type == "ollama":
        adapter = OllamaAdapter(model=model, base_url=base_url)
    elif adapter_type == "oracle":
        adapter = StaticMappingAdapter({sample.input: sample.reference for sample in samples})
    else:
        raise ValueError("adapter must be one of: ollama, oracle")

    evaluator = default_evaluator_registry().create(benchmark.evaluator_type)
    runner = EvaluationRunner(adapter, evaluator)
    results, report = runner.run(job=job, benchmark=benchmark, samples=samples)

    failed_examples = [
        {
            "sample_id": result.sample_id,
            "score": result.score,
            "input": result.input[:800],
            "prediction": result.prediction[:800],
            "reference": result.reference,
            "reason": result.reason,
        }
        for result in results
        if result.score < 1.0
    ][:5]

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
    result = run_real_benchmark(
        dataset=args.dataset,
        adapter_type=args.adapter,
        model=args.model,
        base_url=args.base_url,
        limit=args.limit,
        subject=args.subject,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def serve_command(host: str, port: int) -> int:
    from evalhub.server import serve

    serve(host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalhub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-example", help="Run a deterministic exact-match evaluation.")

    subparsers.add_parser("list-datasets", help="List supported real benchmark datasets.")

    prepare_parser = subparsers.add_parser(
        "prepare-dataset",
        help="Download and cache a real public benchmark dataset locally.",
    )
    prepare_parser.add_argument("dataset", choices=sorted(dataset_catalog()))

    run_parser = subparsers.add_parser(
        "run-benchmark",
        help="Run a local evaluation on a real public benchmark dataset.",
    )
    run_parser.add_argument("--dataset", choices=sorted(dataset_catalog()), default="gsm8k")
    run_parser.add_argument("--adapter", choices=["ollama", "oracle"], default="ollama")
    run_parser.add_argument("--model", default="qwen2.5:0.5b")
    run_parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--subject", default="abstract_algebra")

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the local frontend and backend in one process.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "run-example":
        return run_example()
    if args.command == "list-datasets":
        return list_datasets()
    if args.command == "prepare-dataset":
        return prepare_dataset_command(args.dataset)
    if args.command == "run-benchmark":
        return run_benchmark_command(args)
    if args.command == "serve":
        return serve_command(args.host, args.port)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
