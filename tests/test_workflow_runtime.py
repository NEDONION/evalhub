"""验证系统生成工作流、节点执行、自动重试和部分能力画像。"""

import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

import evalhub.tasks.runtime as runtime_module
import evalhub.tasks.workflow as workflow_module
from evalhub.benchmarks import ExecutorReadiness, get_benchmark_spec
from evalhub.datasets import PinnedSource, hexagon_source_specs
from evalhub.tasks import (
    EvaluationSampleCheckpoint,
    ResourceUsage,
    SQLiteTaskRepository,
    TaskRequest,
)
from evalhub.tasks.executor import TaskExecutionError
from evalhub.tasks.runtime import PersistentWorkflowExecutor, WorkflowIncompleteError
from evalhub.tasks.workflow import build_workflow


def request(*, suite_id: str | None = None, subject: str = "abstract_algebra") -> TaskRequest:
    """构造单项或 Suite 的离线 Oracle 请求。"""
    return TaskRequest(
        dataset="gsm8k",
        adapter="oracle",
        model="oracle",
        base_url="http://127.0.0.1:11434",
        sample_mode="all",
        subject=subject,
        limit=None,
        suite_id=suite_id,
    )


class FakeBenchmarkExecutor:
    """按数据集生成确定性样本事件的内存 Benchmark 执行器。"""

    def __init__(self, *, failures_before_success: int = 0, score: float = 1.0) -> None:
        """配置执行器的瞬时失败次数和固定样本得分。

        Args:
            failures_before_success: 成功返回前需要抛出的可重试错误次数。
            score: 每次样本回调返回的固定得分。
        """
        self.failures_before_success = failures_before_success
        self.score = score
        # 调用次数和恢复跳过集合用于断言重试及断点续跑行为。
        self.attempts: dict[str, int] = {}
        self.seen_skips: list[set[str]] = []

    def execute(
        self,
        task_id: str,
        task_request: TaskRequest,
        *,
        on_progress: object,
        on_resources: object,
        cancel_event: Event,
        skip_sample_ids: set[str] | frozenset[str] = frozenset(),
        on_sample_result: object = None,
    ) -> dict[str, object]:
        """发送一条固定得分样本，并按配置模拟可重试连接错误。

        Args:
            task_id: 当前持久化任务标识。
            task_request: 包含待运行数据集的任务请求。
            on_progress: 节点进度回调。
            on_resources: 资源采样回调。
            cancel_event: 调用方提供的取消事件。
            skip_sample_ids: 恢复时无需再次执行的成功样本标识。
            on_sample_result: 单条样本结果回调。

        Returns:
            包含任务标识和样本总数的最小结果摘要。

        Raises:
            TaskExecutionError: 尚未达到配置的成功尝试次数时抛出。
        """
        dataset = task_request.dataset
        self.attempts[dataset] = self.attempts.get(dataset, 0) + 1
        self.seen_skips.append(set(skip_sample_ids))
        # 在指定次数内模拟连接中断，验证运行时的自动重试路径。
        if self.attempts[dataset] <= self.failures_before_success:
            raise TaskExecutionError("connection reset by peer")
        # 测试执行器要求生产调用方完整提供三类可观察回调。
        assert callable(on_progress)
        assert callable(on_resources)
        assert callable(on_sample_result)
        # 恢复后的进度从已成功样本数开始，并提供一条稳定资源采样。
        on_progress(len(skip_sample_ids), 1)
        on_resources(ResourceUsage(cpu_percent=5.0, memory_bytes=1024))
        # 未被恢复逻辑跳过时才发送样本，得分决定成功或失败语义。
        if f"{dataset}-sample-1" not in skip_sample_ids:
            on_sample_result(
                {
                    "sample_id": f"{dataset}-sample-1",
                    "input": "1 + 1",
                    "prediction": "2",
                    "reference": "2",
                    "metric": "exact_match",
                    "score": self.score,
                    "reason": None if self.score >= 1.0 else "答案不匹配",
                    "metadata": {"input_zh": "一加一", "source_key": "fixture:1"},
                },
                1,
                1,
            )
        # 摘要只服务运行时节点终态，本测试不需要模拟更多指标。
        return {"job_id": task_id, "total_samples": 1}


class CheckpointThenRetryExecutor(FakeBenchmarkExecutor):
    """先提交一个零分结果再模拟瞬时中断，验证检查点不会被重跑覆盖。"""

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """首轮提交结果后失败，第二轮必须直接复用该结果。"""
        dataset = task_request.dataset
        self.attempts[dataset] = self.attempts.get(dataset, 0) + 1
        skipped = set(kwargs["skip_sample_ids"])
        self.seen_skips.append(skipped)
        if not skipped:
            callback = kwargs["on_sample_result"]
            assert callable(callback)
            callback(
                {
                    "sample_id": f"{dataset}-sample-1",
                    "input": "1 + 1",
                    "prediction": "3",
                    "reference": "2",
                    "metric": "exact_match",
                    "score": 0.0,
                    "reason": "答案不匹配",
                },
                1,
                1,
            )
            raise TaskExecutionError("connection reset by peer")
        return {"job_id": task_id, "total_samples": 1}


class MutatingAssetExecutor(FakeBenchmarkExecutor):
    """执行后修改资产，用于证明 Runtime 会拒绝不一致的数据 revision。"""

    def __init__(self, asset: Path) -> None:
        """保存需要在评测窗口内改写的文件。"""
        super().__init__()
        self.asset = asset

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """先完成样本事件，再原子窗口内改写资产内容。"""
        result = super().execute(task_id, task_request, **kwargs)
        self.asset.write_text("changed", encoding="utf-8")
        return result


class MutatingRetryExecutor(FakeBenchmarkExecutor):
    """首轮检查点后改变资产并重试，用于验证旧样本不会进入 skip 集。"""

    def __init__(self, asset: Path) -> None:
        """保存资产路径和执行次数。"""
        super().__init__()
        self.asset = asset

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """第一次写入检查点后中断，第二次应在清空检查点后重新评分。"""
        dataset = task_request.dataset
        self.attempts[dataset] = self.attempts.get(dataset, 0) + 1
        skipped = set(kwargs["skip_sample_ids"])
        self.seen_skips.append(skipped)
        callback = kwargs["on_sample_result"]
        assert callable(callback)
        if self.attempts[dataset] == 1:
            callback(
                {
                    "sample_id": f"{dataset}-sample-1",
                    "input": "1 + 1",
                    "prediction": "3",
                    "reference": "2",
                    "metric": "exact_match",
                    "score": 0.0,
                    "reason": "答案不匹配",
                },
                1,
                1,
            )
            self.asset.write_text("changed", encoding="utf-8")
            raise TaskExecutionError("connection reset by peer")
        callback(
            {
                "sample_id": f"{dataset}-sample-1",
                "input": "1 + 1",
                "prediction": "2",
                "reference": "2",
                "metric": "exact_match",
                "score": 1.0,
                "reason": None,
            },
            1,
            1,
        )
        return {"job_id": task_id, "total_samples": 1}


class MutatingDoubleRetryExecutor(MutatingRetryExecutor):
    """资产变化后的新 revision 再中断一次，用于验证检查点能跨后续重试复用。"""

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """前两次写入后中断；第三次应跳过第二次在新 revision 上完成的样本。"""
        result = super().execute(task_id, task_request, **kwargs)
        if self.attempts[task_request.dataset] == 2:
            raise TaskExecutionError("connection reset by peer")
        return result


class HexagonBenchmarkExecutor(FakeBenchmarkExecutor):
    """按 Registry 固定题数发出七个来源的满分元数据结果。"""

    def execute(
        self, task_id: str, task_request: TaskRequest, **kwargs: object
    ) -> dict[str, object]:
        """为当前 Hexagon 节点发出其声明数量的确定性结果。

        Args:
            task_id: 当前持久化顶层任务标识。
            task_request: 已替换为单个 Hexagon 来源的执行请求。
            **kwargs: Runtime 提供的进度、资源、恢复和样本回调。

        Returns:
            仅供 Runtime 完成子进程调用的增量摘要。
        """
        spec = get_benchmark_spec(task_request.dataset)
        total = int(spec.expected_sample_count or 0)
        skipped = set(kwargs["skip_sample_ids"])
        self.seen_skips.append(skipped)
        progress = kwargs["on_progress"]
        callback = kwargs["on_sample_result"]
        assert callable(progress)
        assert callable(callback)
        progress(len(skipped), total)
        # 每个来源使用独立样本键，确保 SQLite 主键与跨节点恢复都可验证。
        for index in range(total):
            sample_id = f"{task_request.dataset}-{index + 1}"
            if sample_id in skipped:
                continue
            metadata: dict[str, object] = {
                "input_zh": f"中文题目 {index + 1}",
                "source_key": f"{task_request.dataset}:{index + 1}",
            }
            # HumanEval 假执行器镜像真实安全溯源形状，验证通用检查点不会截断可展示字段。
            if task_request.dataset == "hexagon-humaneval":
                metadata.update(
                    {
                        "dataset": "hexagon-humaneval",
                        "selection_stratum": f"HumanEval/{index + 1}",
                        "evaluator_type": "pass@1",
                        "reference_zh": None,
                        "translation_version": "evalhub-zh-v1",
                        "input_sha256": "a" * 64,
                        "reference_sha256": "b" * 64,
                    }
                )
            callback(
                {
                    "sample_id": sample_id,
                    "input": f"English prompt {index + 1}",
                    "prediction": "A",
                    "reference": "A",
                    "metric": spec.metric,
                    "score": 1.0,
                    "reason": None,
                    "metadata": metadata,
                },
                index + 1,
                total,
            )
        return {"job_id": task_id, "total_samples": total, "incremental": bool(skipped)}


def test_single_benchmark_builds_fixed_four_node_graph() -> None:
    """兼容请求应映射为准备、单项、聚合和终结四个节点。"""
    graph = build_workflow(request())

    assert [node.node_key for node in graph] == [
        "prepare_assets",
        "benchmark:gsm8k",
        "capability_aggregate",
        "workflow_finalize",
    ]
    assert graph[2].depends_on == ("benchmark:gsm8k",)


def test_suite_builds_one_benchmark_node_per_registry_member() -> None:
    """行业 Suite 必须保持 Registry 顺序生成全部 Benchmark 节点。"""
    graph = build_workflow(request(suite_id="llm-industry-core-v1"))
    benchmark_keys = [node.node_key for node in graph if node.kind == "benchmark"]

    assert benchmark_keys[0] == "benchmark:mmlu-pro"
    assert "benchmark:gsm8k" in benchmark_keys
    assert "benchmark:humaneval" in benchmark_keys
    assert graph[-2].node_key == "capability_aggregate"
    assert graph[-1].node_key == "workflow_finalize"


def test_suite_freezes_mmlu_to_all_subjects() -> None:
    """版本化行业 Suite 不能继承单项表单中残留的 MMLU 学科。"""
    graph = build_workflow(request(suite_id="llm-industry-core-v1", subject="abstract_algebra"))
    mmlu = next(node for node in graph if node.node_key == "benchmark:mmlu")

    assert mmlu.input["subject"] == "all"


def test_hexagon_workflow_has_seven_revisioned_benchmark_nodes_for_sixty_samples() -> None:
    """Hexagon 工作流应冻结七个来源的题数、revision、提示版本和生成配置。"""
    graph = build_workflow(request(suite_id="evalhub-hexagon-v1"))
    benchmarks = [node for node in graph if node.kind == "benchmark"]

    assert len(benchmarks) == 7
    assert sum(int(node.input["expected_sample_count"]) for node in benchmarks) == 60
    assert all(node.input["prompt_template_version"] == "evalhub-v1" for node in benchmarks)
    assert all(
        node.input["generation_config"] == {"temperature": 0, "num_predict": 256}
        for node in benchmarks
    )
    ifeval = next(node for node in benchmarks if node.input["benchmark_id"] == "hexagon-ifeval")
    assert ifeval.input["dataset_revision"] == "8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0"


def test_hexagon_protocol_fingerprint_changes_for_every_frozen_revision_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清单、Suite、提示或生成配置变化都必须产生不同的完整协议指纹。"""
    task_request = request(suite_id="evalhub-hexagon-v1")
    real_suite = workflow_module.get_suite_spec("evalhub-hexagon-v1")
    real_get_benchmark = workflow_module.get_benchmark_spec

    def fingerprint() -> str:
        """读取新建工作流七个 Benchmark 共享的冻结协议指纹。"""
        graph = workflow_module.build_workflow(task_request)
        fingerprints = {
            str(node.input.get("protocol_fingerprint"))
            for node in graph
            if node.kind == "benchmark"
        }
        assert len(fingerprints) == 1
        return fingerprints.pop()

    monkeypatch.setattr(
        workflow_module,
        "_hexagon_manifest_sha256",
        lambda: "a" * 64,
        raising=False,
    )
    baseline = fingerprint()
    monkeypatch.setattr(workflow_module, "_hexagon_manifest_sha256", lambda: "b" * 64)
    changed_manifest = fingerprint()
    monkeypatch.setattr(workflow_module, "_hexagon_manifest_sha256", lambda: "a" * 64)
    monkeypatch.setattr(
        workflow_module,
        "get_suite_spec",
        lambda suite_id: replace(real_suite, version="1.0.1"),
    )
    changed_suite = fingerprint()
    monkeypatch.setattr(workflow_module, "get_suite_spec", lambda suite_id: real_suite)

    def changed_prompt(benchmark_id: str):
        """只改变 IFEval 提示模板，其他 Benchmark 保持真实冻结规格。"""
        spec = real_get_benchmark(benchmark_id)
        if benchmark_id == "hexagon-ifeval":
            return replace(spec, prompt_template_version="evalhub-v2")
        return spec

    monkeypatch.setattr(workflow_module, "get_benchmark_spec", changed_prompt)
    changed_prompt_fingerprint = fingerprint()

    def changed_generation(benchmark_id: str):
        """只改变 HumanEval 生成上限，验证不可变生成配置参与指纹。"""
        spec = real_get_benchmark(benchmark_id)
        if benchmark_id == "hexagon-humaneval":
            return replace(spec, generation_config={"temperature": 0, "num_predict": 512})
        return spec

    monkeypatch.setattr(workflow_module, "get_benchmark_spec", changed_generation)
    changed_generation_fingerprint = fingerprint()
    # 固定来源下载合同也是协议本体；只改变 URL 必须使同字节清单获得不同指纹。
    changed_sources = hexagon_source_specs()
    current_gsm8k = changed_sources["hexagon-gsm8k"]
    changed_sources["hexagon-gsm8k"] = replace(
        current_gsm8k,
        url=f"{current_gsm8k.url}?deployment=b",
    )
    monkeypatch.setattr(
        workflow_module,
        "hexagon_source_specs",
        lambda: changed_sources,
        raising=False,
    )
    changed_source_fingerprint = fingerprint()

    assert len(
        {
            baseline,
            changed_manifest,
            changed_suite,
            changed_prompt_fingerprint,
            changed_generation_fingerprint,
            changed_source_fingerprint,
        }
    ) == 6


def test_hexagon_workflow_freezes_complete_pinned_source_contracts() -> None:
    """Hexagon 创建结果必须冻结 Task 2 下载边界并用于最终复现。

    Returns:
        无；通过节点输入与终结复现字段断言来源 ID、URL、revision 和摘要均已冻结。
    """
    graph = build_workflow(request(suite_id="evalhub-hexagon-v1"))
    benchmarks = [node for node in graph if node.kind == "benchmark"]
    finalizer = next(node for node in graph if node.kind == "workflow_finalize")
    # 使用手工核对的 GSM8K 固定值，避免测试与生产序列化辅助函数共享同一错误。
    gsm8k = next(node for node in benchmarks if node.input["benchmark_id"] == "hexagon-gsm8k")
    expected = {
        "source_id": "hexagon-gsm8k",
        "url": (
            "https://raw.githubusercontent.com/openai/grade-school-math/"
            "3101c7d5072418e28b9008a6636bde82a006892c/"
            "grade_school_math/data/test.jsonl"
        ),
        "revision": "3101c7d5072418e28b9008a6636bde82a006892c",
        "sha256": "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14",
    }

    assert gsm8k.input["source_contract"] == expected
    assert finalizer.input["reproducibility"]["source_contracts"]["hexagon-gsm8k"] == expected


def test_runtime_blocks_hexagon_source_drift_before_any_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """排队后固定来源漂移时必须先阻塞整个准备节点，不能下载或执行。

    Args:
        tmp_path: pytest 隔离数据库与伪资产目录。
        monkeypatch: 模拟部署 B 的 Task 2 固定来源记录，同时保持包内清单字节不变。

    Returns:
        无；断言下载和 Benchmark 边界均未被调用，且最终只发布创建时冻结事实。
    """
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="evalhub-hexagon-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    created_nodes = repository.list_nodes(task.id)
    finalizer = next(node for node in created_nodes if node.kind == "workflow_finalize")
    frozen_reproducibility = finalizer.input["reproducibility"]
    frozen_manifest = frozen_reproducibility["manifest_sha256"]
    # 只改变第三个来源记录，证明运行时必须在任何下载前完成全套来源预检。
    deployed_sources = hexagon_source_specs()
    old_gsm8k = deployed_sources["hexagon-gsm8k"]
    deployed_sources["hexagon-gsm8k"] = replace(
        old_gsm8k,
        revision="deployment-b",
        url=f"{old_gsm8k.url}?deployment=b",
        sha256="f" * 64,
    )
    monkeypatch.setattr(
        runtime_module,
        "hexagon_source_specs",
        lambda: deployed_sources,
        raising=False,
    )
    monkeypatch.setattr(runtime_module, "_hexagon_manifest_sha256", lambda: frozen_manifest)
    preparation_calls: list[str] = []
    fake = FakeBenchmarkExecutor()
    asset = tmp_path / "must-not-be-prepared"
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: preparation_calls.append(benchmark_id) or asset,
        readiness_checker=lambda spec: ExecutorReadiness(True, "ready", "fixture ready"),
    )

    with pytest.raises(WorkflowIncompleteError):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: None,
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )
    nodes = repository.list_nodes(task.id)
    prepare = next(node for node in nodes if node.kind == "prepare_assets")
    benchmarks = [node for node in nodes if node.kind == "benchmark"]
    completed_finalizer = next(node for node in nodes if node.kind == "workflow_finalize")

    assert preparation_calls == []
    assert fake.attempts == {}
    assert prepare.status == "blocked"
    assert prepare.error_type == "source_contract_changed"
    assert all(node.status == "blocked" and node.output is None for node in benchmarks)
    assert completed_finalizer.output["total_samples"] == 0
    assert completed_finalizer.output["reproducibility"] == frozen_reproducibility


def test_runtime_prepares_hexagon_when_frozen_source_contract_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当前 Task 2 来源与冻结合同一致时应正常准备并执行七个 Benchmark。

    Args:
        tmp_path: pytest 隔离数据库与固定字节伪资产目录。
        monkeypatch: 记录运行时是否实际读取当前部署的来源合同。

    Returns:
        无；断言完整预检只读取一次目录，并允许全部七个准备与执行调用。
    """
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="evalhub-hexagon-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    current_sources = hexagon_source_specs()
    source_catalog_reads = 0

    def read_current_sources() -> dict[str, PinnedSource]:
        """记录一次部署来源目录读取并返回与创建时一致的不可变记录。

        Returns:
            当前测试部署的七条 Task 2 固定来源记录副本。
        """
        nonlocal source_catalog_reads
        source_catalog_reads += 1
        return dict(current_sources)

    monkeypatch.setattr(runtime_module, "hexagon_source_specs", read_current_sources, raising=False)
    preparation_calls: list[str] = []
    fake = FakeBenchmarkExecutor()
    asset = tmp_path / "hexagon-source"
    asset.write_text("fixed source", encoding="utf-8")
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: preparation_calls.append(benchmark_id) or asset,
        readiness_checker=lambda spec: ExecutorReadiness(True, "ready", "fixture ready"),
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert source_catalog_reads == 1
    assert preparation_calls == list(current_sources)
    assert set(fake.attempts) == set(current_sources)
    assert result["status"] == "success"


def test_runtime_reruns_same_bytes_checkpoint_when_protocol_fingerprint_changed(
    tmp_path: Path,
) -> None:
    """来源字节未变但协议指纹不同时，旧样本不得进入跳过集合或最终聚合。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    prepare, benchmark = repository.list_nodes(task.id)[:2]
    frozen_fingerprint = benchmark.input.get("protocol_fingerprint")
    assert isinstance(frozen_fingerprint, str)
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("unchanged source bytes", encoding="utf-8")
    content_sha256 = hashlib.sha256(asset.read_bytes()).hexdigest()

    repository.start_node(prepare.id)
    repository.complete_node(
        prepare.id,
        {
            "assets": {
                "gsm8k": {
                    "status": "ready",
                    "path": str(asset),
                    "content_sha256": content_sha256,
                    "dataset_revision": f"sha256:{content_sha256}",
                }
            }
        },
    )
    running = repository.start_node(benchmark.id)
    repository.record_sample(
        running.id,
        EvaluationSampleCheckpoint(
            node_id=running.id,
            sample_key="gsm8k-sample-1",
            sample_index=0,
            status="success",
            attempt_count=1,
            input={"input": "1 + 1", "protocol_fingerprint": "stale-protocol"},
            result={
                "sample_id": "gsm8k-sample-1",
                "metric": "exact_match",
                "score": 1.0,
                "protocol_fingerprint": "stale-protocol",
            },
        ),
        completed=1,
        total=1,
        content_sha256=content_sha256,
    )
    repository.reschedule_node(running.id, "connection_reset", "fixture retry")
    fake = FakeBenchmarkExecutor(score=0.0)
    runtime = PersistentWorkflowExecutor(repository, benchmark_executor=fake)

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    restored = repository.list_samples(running.id).items

    assert fake.seen_skips == [set()]
    assert result["average_score"] == 0.0
    assert len(restored) == 1
    assert restored[0].result["protocol_fingerprint"] == frozen_fingerprint


def test_runtime_uses_creation_frozen_protocol_after_registry_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务创建后 Registry 变化时，执行产物和最终复现信息仍使用冻结事实。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="evalhub-hexagon-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    created_nodes = repository.list_nodes(task.id)
    finalizer = next(node for node in created_nodes if node.kind == "workflow_finalize")
    frozen_reproducibility = finalizer.input.get("reproducibility")
    frozen_fingerprint = finalizer.input.get("protocol_fingerprint")
    assert isinstance(frozen_reproducibility, dict)
    assert isinstance(frozen_fingerprint, str)
    asset = tmp_path / "hexagon-source"
    asset.write_text("unchanged source bytes", encoding="utf-8")
    real_runtime_get = get_benchmark_spec
    current_suite = workflow_module.get_suite_spec("evalhub-hexagon-v1")

    def changed_spec(benchmark_id: str):
        """模拟部署升级后 Registry 中同 ID 的来源与提示协议发生变化。"""
        spec = real_runtime_get(benchmark_id)
        return replace(
            spec,
            version="9.9.9",
            dataset_revision="future-source-revision",
            prompt_template_version="future-prompt",
            generation_config={"temperature": 0, "num_predict": 999},
        )

    monkeypatch.setattr(runtime_module, "get_benchmark_spec", changed_spec, raising=False)
    monkeypatch.setattr(
        runtime_module,
        "workflow_suite",
        lambda request: replace(current_suite, version="9.9.9", display_name="Future Suite"),
        raising=False,
    )
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=HexagonBenchmarkExecutor(),
        asset_preparer=lambda benchmark_id: asset,
        readiness_checker=lambda spec: ExecutorReadiness(True, "ready", "fixture ready"),
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    benchmark_outputs = [
        node.output
        for node in repository.list_nodes(task.id)
        if node.kind == "benchmark"
    ]

    assert result["reproducibility"] == frozen_reproducibility
    assert result["comparison_fingerprint"] == frozen_fingerprint
    assert result["benchmark"] == "EvalHub 专业六边形套件 v1"
    assert result["capability_profile"]["suite_version"] == "1.0.0"
    assert all(output["protocol_fingerprint"] == frozen_fingerprint for output in benchmark_outputs)
    assert all(output["prompt_template_version"] == "evalhub-v1" for output in benchmark_outputs)


def test_runtime_blocks_same_bytes_resume_when_packaged_manifest_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """来源字节未变但包内清单漂移时，旧检查点必须清空且不得送入跳过集合。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="evalhub-hexagon-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    nodes = repository.list_nodes(task.id)
    prepare = next(node for node in nodes if node.kind == "prepare_assets")
    benchmarks = [node for node in nodes if node.kind == "benchmark"]
    first = benchmarks[0]
    frozen_fingerprint = str(first.input["protocol_fingerprint"])
    frozen_reproducibility = next(
        node for node in nodes if node.kind == "workflow_finalize"
    ).input["reproducibility"]
    asset = tmp_path / "hexagon-source"
    asset.write_text("unchanged source bytes", encoding="utf-8")
    content_sha256 = hashlib.sha256(asset.read_bytes()).hexdigest()
    repository.start_node(prepare.id)
    repository.complete_node(
        prepare.id,
        {
            "assets": {
                str(node.input["benchmark_id"]): {
                    "status": "ready",
                    "path": str(asset),
                    "content_sha256": content_sha256,
                    "dataset_revision": node.input["dataset_revision"],
                }
                for node in benchmarks
            }
        },
    )
    running = repository.start_node(first.id)
    repository.record_sample(
        running.id,
        EvaluationSampleCheckpoint(
            node_id=running.id,
            sample_key="hexagon-mmlu-1",
            sample_index=0,
            status="success",
            attempt_count=1,
            input={"protocol_fingerprint": frozen_fingerprint},
            result={
                "sample_id": "hexagon-mmlu-1",
                "score": 1.0,
                "protocol_fingerprint": frozen_fingerprint,
            },
        ),
        completed=1,
        total=10,
        content_sha256=content_sha256,
    )
    repository.reschedule_node(running.id, "connection_reset", "fixture retry")
    fake = FakeBenchmarkExecutor()
    monkeypatch.setattr(
        runtime_module,
        "_hexagon_manifest_sha256",
        lambda: "f" * 64,
        raising=False,
    )
    runtime = PersistentWorkflowExecutor(repository, benchmark_executor=fake)

    with pytest.raises(WorkflowIncompleteError):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: None,
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )

    assert fake.seen_skips == []
    assert repository.list_samples(first.id).items == ()
    finalizer = next(
        node for node in repository.list_nodes(task.id) if node.kind == "workflow_finalize"
    )
    assert finalizer.output["reproducibility"] == frozen_reproducibility
    assert finalizer.output["total_samples"] == 0


def test_hexagon_runtime_persists_sixty_metadata_results_and_reproducibility(
    tmp_path: Path,
) -> None:
    """七节点 Oracle 流程应持久化 60 条溯源结果并生成完整六维复现信息。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="evalhub-hexagon-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "hexagon-source"
    asset.write_text("fixed source", encoding="utf-8")
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=HexagonBenchmarkExecutor(),
        asset_preparer=lambda benchmark_id: asset,
        readiness_checker=lambda spec: ExecutorReadiness(True, "ready", "fixture ready"),
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    benchmarks = [node for node in repository.list_nodes(task.id) if node.kind == "benchmark"]
    samples = [
        sample
        for node in benchmarks
        for sample in repository.list_samples(node.id, limit=200).items
    ]

    assert len(samples) == 60
    assert all((sample.result or {})["metadata"]["input_zh"] for sample in samples)
    assert all((sample.input or {})["metadata"]["source_key"] for sample in samples)
    humaneval = next(
        node for node in benchmarks if node.input["benchmark_id"] == "hexagon-humaneval"
    )
    humaneval_sample = repository.list_samples(humaneval.id).items[0]
    humaneval_metadata = humaneval_sample.result["metadata"]
    assert humaneval_metadata["dataset"] == "hexagon-humaneval"
    assert humaneval_metadata["reference_zh"] is None
    assert humaneval_metadata["translation_version"] == "evalhub-zh-v1"
    assert "canonical_solution" not in humaneval_metadata
    assert "test" not in humaneval_metadata
    assert result["total_samples"] == 60
    assert result["capability_profile"]["status"] == "complete"
    assert {
        capability["score"]
        for capability in result["capability_profile"]["capabilities"].values()
    } == {100.0}
    reproducibility = result["reproducibility"]
    assert len(result["comparison_fingerprint"]) == 64
    assert reproducibility["suite_version"] == "1.0.0"
    assert reproducibility["manifest_sha256"] == (
        "9ff977a258c61dacb568d1fc5d30209fa340687ee2e1be8e7365f5a18df2b6f2"
    )
    assert reproducibility["source_revisions"]["hexagon-ifeval"] == (
        "8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0"
    )
    assert reproducibility["prompt_template_versions"] == {
        node.input["benchmark_id"]: "evalhub-v1" for node in benchmarks
    }
    assert reproducibility["generation_config"] == {"temperature": 0, "num_predict": 256}


def test_hexagon_runtime_blocks_unready_humaneval_without_executing_it(tmp_path: Path) -> None:
    """Docker 未就绪时 HumanEval 节点必须阻塞，不能调用模型或产生零分样本。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="evalhub-hexagon-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = FakeBenchmarkExecutor()
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
        readiness_checker=lambda spec: ExecutorReadiness(
            spec.id != "hexagon-humaneval",
            "ready" if spec.id != "hexagon-humaneval" else "executor_not_ready",
            "fixture Docker missing",
        ),
    )

    with pytest.raises(WorkflowIncompleteError):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: None,
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )

    humaneval = next(
        node
        for node in repository.list_nodes(task.id)
        if node.node_key == "benchmark:hexagon-humaneval"
    )
    assert humaneval.status == "blocked"
    assert humaneval.error_type == "executor_not_ready"
    assert "hexagon-humaneval" not in fake.attempts
    assert repository.completed_sample_keys(humaneval.id) == set()


def test_runtime_persists_samples_and_completes_single_benchmark(tmp_path: Path) -> None:
    """单项 Oracle 流程应成功持久化样本、画像和最终结果。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = FakeBenchmarkExecutor()
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text('{"question":"1+1","answer":"2"}\n', encoding="utf-8")
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: asset,
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    nodes = repository.list_nodes(task.id)
    benchmark = next(node for node in nodes if node.kind == "benchmark")
    prepare = next(node for node in nodes if node.kind == "prepare_assets")
    aggregate = next(node for node in nodes if node.kind == "capability_aggregate")

    assert {node.status for node in nodes} == {"success"}
    assert repository.successful_sample_keys(benchmark.id) == {"gsm8k-sample-1"}
    expected_digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    assert prepare.output["assets"]["gsm8k"]["content_sha256"] == expected_digest
    assert benchmark.output["dataset_revision"] == f"sha256:{expected_digest}"
    assert aggregate.output is not None
    assert aggregate.output["status"] == "partial"
    assert aggregate.output["capabilities"]["mathematics"]["score"] == 100.0
    assert aggregate.output["capabilities"]["knowledge"]["score"] is None
    assert result["total_samples"] == 1
    assert result["average_score"] == 1.0


def test_runtime_retries_transient_benchmark_error_three_times(tmp_path: Path) -> None:
    """瞬时连接错误应回到 pending，并在第三次尝试成功。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = FakeBenchmarkExecutor(failures_before_success=2)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    benchmark = next(node for node in repository.list_nodes(task.id) if node.kind == "benchmark")

    assert fake.attempts == {"gsm8k": 3}
    assert benchmark.status == "success"
    assert benchmark.attempt_count == 3
    assert [event.event_type for event in repository.list_node_events(benchmark.id)].count(
        "node_retry_scheduled"
    ) == 2


def test_runtime_marks_scored_failure_as_completed_debuggable_sample(tmp_path: Path) -> None:
    """得分未通过的样本应进入失败分页，但断点恢复时不能重新推理。

    Args:
        tmp_path: pytest 提供的隔离目录，用于创建临时 SQLite 仓储。
    """
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    # 固定返回零分样本，直接覆盖失败判定而不依赖真实模型或网络。
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=FakeBenchmarkExecutor(score=0.0),
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    # 完整执行持久化工作流，确保样本状态经过真实运行时和仓储边界。
    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )
    # 读取 Benchmark 节点的恢复集合和失败分页，验证两个公开查询保持一致。
    benchmark = next(node for node in repository.list_nodes(task.id) if node.kind == "benchmark")
    failed_page = repository.list_samples(benchmark.id, status="failed")

    assert repository.successful_sample_keys(benchmark.id) == set()
    assert repository.completed_sample_keys(benchmark.id) == {"gsm8k-sample-1"}
    assert [sample.sample_key for sample in failed_page.items] == ["gsm8k-sample-1"]
    assert failed_page.items[0].result["reason"] == "答案不匹配"
    assert result["failed_examples"][0]["metadata"] == {
        "input_zh": "一加一",
        "source_key": "fixture:1",
    }


def test_runtime_retry_reuses_completed_zero_score_checkpoint(tmp_path: Path) -> None:
    """瞬时错误后的节点重试必须复用已经评分的零分样本。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    fake = CheckpointThenRetryExecutor()
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert fake.seen_skips == [set(), {"gsm8k-sample-1"}]
    assert result["average_score"] == 0.0


def test_runtime_blocks_when_dataset_changes_during_benchmark(tmp_path: Path) -> None:
    """评测窗口内资产摘要变化时不能产出声称可复现的 Benchmark 结果。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("original", encoding="utf-8")
    repository.mark_running(task.id)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=MutatingAssetExecutor(asset),
        asset_preparer=lambda benchmark_id: asset,
    )

    with pytest.raises(WorkflowIncompleteError):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: repository.update_progress(
                task.id, completed=completed, total=total
            ),
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )

    benchmark = next(node for node in repository.list_nodes(task.id) if node.kind == "benchmark")
    assert benchmark.status == "blocked"
    assert benchmark.error_type == "dataset_revision_changed"
    assert repository.completed_sample_keys(benchmark.id) == set()
    assert repository.get(task.id).completed_samples == 0
    assert repository.get(task.id).total_samples == 0


def test_runtime_invalidates_checkpoints_when_asset_changes_between_retries(
    tmp_path: Path,
) -> None:
    """准备后发生资产变化时，旧 revision 的样本不能被恢复逻辑跳过。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("original", encoding="utf-8")
    fake = MutatingRetryExecutor(asset)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: asset,
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert fake.seen_skips == [set(), set()]
    assert result["average_score"] == 1.0


def test_runtime_reuses_new_revision_checkpoints_on_later_retry(tmp_path: Path) -> None:
    """资产切换到新 revision 后形成的检查点必须在下一次瞬时重试中复用。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request()
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    asset = tmp_path / "gsm8k.jsonl"
    asset.write_text("original", encoding="utf-8")
    fake = MutatingDoubleRetryExecutor(asset)
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=fake,
        asset_preparer=lambda benchmark_id: asset,
    )

    result = runtime.execute(
        task.id,
        task_request,
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert fake.seen_skips == [set(), set(), {"gsm8k-sample-1"}]
    assert result["average_score"] == 1.0


def test_core_suite_blocks_unavailable_executors_but_builds_partial_profile(
    tmp_path: Path,
) -> None:
    """本地缺少外部执行器时应保留原生结果并产生部分画像。"""
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task_request = request(suite_id="llm-industry-core-v1")
    task = repository.create_with_nodes(task_request, build_workflow(task_request))
    runtime = PersistentWorkflowExecutor(
        repository,
        benchmark_executor=FakeBenchmarkExecutor(),
        asset_preparer=lambda benchmark_id: f"/cache/{benchmark_id}",
    )

    with pytest.raises(WorkflowIncompleteError, match="部分 Benchmark 未完成"):
        runtime.execute(
            task.id,
            task_request,
            on_progress=lambda completed, total: None,
            on_resources=lambda usage: None,
            cancel_event=Event(),
        )
    nodes = repository.list_nodes(task.id)
    profile = next(node.output for node in nodes if node.kind == "capability_aggregate")

    assert next(node for node in nodes if node.node_key == "benchmark:gsm8k").status == "success"
    assert (
        next(node for node in nodes if node.node_key == "benchmark:humaneval").status == "blocked"
    )
    assert profile is not None
    assert profile["status"] == "partial"
    assert profile["capabilities"]["coding"]["score"] is None
