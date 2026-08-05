"""验证隔离评测执行器的子进程消息与异常退出处理。"""

from dataclasses import asdict, replace
from queue import Empty, Queue
from threading import Event, Thread
from typing import cast

import pytest

import evalhub.tasks.executor as executor_module
from evalhub.adapters import ModelGenerationError
from evalhub.benchmarks import HumanEvalProblem, SandboxInfrastructureError
from evalhub.domain import EvaluationSampleResult
from evalhub.tasks import ResourceUsage, TaskRequest
from evalhub.tasks.executor import (
    SubprocessEvaluationExecutor,
    TaskExecutionCanceled,
    TaskExecutionError,
    _evaluation_process,
)


def request_fixture() -> TaskRequest:
    """构造子进程入口可直接执行的离线 quick 请求。"""
    return TaskRequest(
        dataset="gsm8k",
        adapter="oracle",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        sample_mode="quick",
        subject="abstract_algebra",
        limit=None,
    )


class RecordingQueue:
    """记录子进程入口发送事件的内存队列。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[dict[str, object]] = []

    def put(self, value: dict[str, object]) -> None:
        """按发送顺序保存一个 JSON 兼容事件。"""
        self.events.append(value)


def test_evaluation_process_reports_progress_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """子进程入口应沿用任务 ID，把 quick 转成 5 条并发送最终结果。"""
    event_queue = RecordingQueue()
    observed: dict[str, object] = {}

    def fake_benchmark(**kwargs: object) -> dict[str, object]:
        """记录执行参数并通过真实回调通道发送两个进度事件。"""
        observed.update(kwargs)
        on_progress = kwargs["on_progress"]
        on_progress(0, 5)
        on_progress(5, 5)
        return {"job_id": kwargs["job_id"], "total_samples": 5}

    monkeypatch.setattr(executor_module, "run_real_benchmark", fake_benchmark)
    _evaluation_process("job_process", asdict(request_fixture()), event_queue)

    assert observed["job_id"] == "job_process"
    assert observed["limit"] == 5
    assert event_queue.events == [
        {"type": "progress", "completed": 0, "total": 5},
        {"type": "progress", "completed": 5, "total": 5},
        {"type": "result", "result": {"job_id": "job_process", "total_samples": 5}},
    ]


def test_evaluation_process_forwards_api_provider_to_native_benchmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原生评测应把服务商引用传到模型构造边界，任务载荷不得含 API Key。"""
    request = replace(
        request_fixture(),
        adapter="openai-compatible",
        provider_id="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
    )
    event_queue = RecordingQueue()
    observed: dict[str, object] = {}

    def fake_benchmark(**kwargs: object) -> dict[str, object]:
        """记录原生执行参数而不连接真实模型服务。"""
        observed.update(kwargs)
        return {"job_id": kwargs["job_id"], "total_samples": 0}

    monkeypatch.setattr(executor_module, "run_real_benchmark", fake_benchmark)
    payload = asdict(request)
    _evaluation_process("job_api", payload, event_queue)

    assert observed["provider_id"] == "deepseek"
    assert observed["base_url"] == "https://api.deepseek.com"
    assert all("api_key" not in key for key in payload)
    assert event_queue.events[-1]["type"] == "result"


def test_evaluation_process_rejects_api_provider_for_harness_benchmark() -> None:
    """首期未接入的核心 Harness 路径应明确失败，不能产生虚假成功结果。"""
    request = replace(
        request_fixture(),
        dataset="ifeval",
        adapter="openai-compatible",
        provider_id="deepseek",
        base_url="https://api.deepseek.com",
    )
    event_queue = RecordingQueue()

    _evaluation_process("job_api_harness", asdict(request), event_queue)

    assert event_queue.events == [
        {
            "type": "error",
            "message": "IFEval暂不支持 API 服务商；仅支持 Ollama 本地模型",
        }
    ]


def test_evaluation_process_dispatches_lm_eval_benchmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry 外部任务应进入 Harness，并继续使用既有样本事件协议。"""
    request = replace(
        request_fixture(),
        dataset="ifeval",
        adapter="ollama",
        model="qwen2.5:0.5b",
        sample_mode="all",
    )
    event_queue = RecordingQueue()
    observed: dict[str, object] = {}

    def fake_harness(**kwargs: object) -> dict[str, object]:
        """记录外部评测参数并发送一条 JSON 样本结果。"""
        observed.update(kwargs)
        on_sample_result = kwargs["on_sample_result"]
        assert callable(on_sample_result)
        on_sample_result(
            {
                "sample_id": "ifeval:0",
                "input": {"prompt": "Follow exactly"},
                "prediction": "ok",
                "reference": "ok",
                "metric": "prompt_level_strict_acc",
                "score": 1.0,
                "reason": None,
            },
            1,
            1,
        )
        return {"benchmark_id": "ifeval", "raw_score": 1.0, "total_samples": 1}

    monkeypatch.setattr(executor_module, "run_harness_benchmark", fake_harness, raising=False)
    _evaluation_process("job_ifeval", asdict(request), event_queue)

    assert observed["benchmark_id"] == "ifeval"
    assert observed["limit"] is None
    assert event_queue.events[0]["type"] == "sample_result"
    assert event_queue.events[-1]["result"]["benchmark_id"] == "ifeval"


def test_evaluation_process_dispatches_agent_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pi Agent 请求应把难度原样交给 Coding Mini，且不再传旧 limit。"""
    request = replace(
        request_fixture(),
        evaluation_type="agent",
        agent_framework="pi",
        dataset="coding_mini",
        adapter="ollama",
        sample_mode="all",
        agent_difficulty="hard",
    )
    event_queue = RecordingQueue()
    observed: dict[str, object] = {}

    def fake_agent_benchmark(**kwargs: object) -> dict[str, object]:
        """记录 Agent 参数，通过公开回调发送事件并返回最小公共结果。"""
        observed.update(kwargs)
        on_trace = kwargs["on_trace"]
        on_trace(
            {
                "event_type": "sample_started",
                "actor": "benchmark",
                "message": "Fix pricing.total_with_tax",
                "payload": {"sample_id": "pricing_total"},
            }
        )
        return {
            "job_id": kwargs["job_id"],
            "evaluation_type": "agent",
            "total_samples": 2,
        }

    monkeypatch.setattr(executor_module, "run_pi_agent_benchmark", fake_agent_benchmark)
    _evaluation_process("job_agent", asdict(request), event_queue)

    assert observed["job_id"] == "job_agent"
    assert observed["difficulty"] == "hard"
    assert "limit" not in observed
    assert observed["model"] == "local-test"
    assert event_queue.events[-2] == {
        "type": "trace_event",
        "event": {
            "event_type": "sample_started",
            "actor": "benchmark",
            "message": "Fix pricing.total_with_tax",
            "payload": {"sample_id": "pricing_total"},
        },
    }
    assert event_queue.events[-1]["result"]["evaluation_type"] == "agent"


def test_executor_forwards_trace_event_to_parent_callback() -> None:
    """父进程读取 trace_event 时应把完整白名单事件交给任务服务。"""
    event_queue: Queue[dict[str, object]] = Queue()
    event = {
        "event_type": "workspace_changed",
        "actor": "benchmark",
        "message": "无受控文件变化",
        "payload": {"sample_id": "pricing_total", "changed_files": []},
    }
    event_queue.put({"type": "trace_event", "event": event})
    observed: list[dict[str, object]] = []

    result, error = SubprocessEvaluationExecutor._read_event(
        event_queue,
        result=None,
        error_message=None,
        on_progress=lambda completed, total: None,
        on_sample_result=None,
        on_trace=observed.append,
    )

    assert result is None
    assert error is None
    assert observed == [event]


def test_evaluation_process_serializes_sample_result_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子进程应在终态结果前发送可持久化的样本级结果事件。"""
    event_queue = RecordingQueue()

    def fake_benchmark(**kwargs: object) -> dict[str, object]:
        """通过公开回调发送一条真实领域样本结果。"""
        on_sample_result = kwargs["on_sample_result"]
        on_sample_result(
            EvaluationSampleResult(
                job_id="job_process",
                sample_id="sample-1",
                input="1 + 1",
                prediction="2",
                reference="2",
                metric="exact_match",
                score=1.0,
                metadata={"input_zh": "一道题", "source_key": "fixture:1"},
            ),
            1,
            1,
        )
        return {"job_id": "job_process", "total_samples": 1}

    monkeypatch.setattr(executor_module, "run_real_benchmark", fake_benchmark)
    _evaluation_process(
        "job_process",
        asdict(request_fixture()),
        event_queue,
        ("already-complete",),
    )

    assert event_queue.events[0] == {
        "type": "sample_result",
        "completed": 1,
        "total": 1,
        "sample": {
            "sample_id": "sample-1",
            "input": "1 + 1",
            "prediction": "2",
            "reference": "2",
            "metric": "exact_match",
            "score": 1.0,
            "reason": None,
            "metadata": {"input_zh": "一道题", "source_key": "fixture:1"},
        },
    }
    assert event_queue.events[-1]["type"] == "result"


def test_evaluation_process_dispatches_only_hexagon_humaneval_to_specialized_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hexagon HumanEval 应复用模型边界并把脱敏字典事件交给现有持久化通道。"""
    request = replace(
        request_fixture(),
        dataset="hexagon-humaneval",
        adapter="oracle",
        sample_mode="all",
    )
    problem = HumanEvalProblem(
        sample_id="hexagon_humaneval_01",
        prompt="def one():\n",
        canonical_solution="    return 1\n",
        test="def check(candidate):\n    assert candidate() == 1\n",
        entry_point="one",
        metadata={
            "dataset": "hexagon-humaneval",
            "source_key": "HumanEval/1",
            "selection_stratum": "HumanEval/1",
            "evaluator_type": "pass@1",
            "input_zh": "实现 one",
            "reference_zh": None,
            "translation_version": "evalhub-zh-v1",
        },
    )
    event_queue = RecordingQueue()
    observed: dict[str, object] = {}

    def fake_humaneval(**kwargs: object) -> dict[str, object]:
        """记录专用调用参数并发出一条包含展示元数据的脱敏结果。"""
        observed.update(kwargs)
        adapter = kwargs["adapter"]
        observed["oracle_prediction"] = adapter.generate(problem.prompt)
        callback = kwargs["on_sample_result"]
        callback(
            {
                "sample_id": problem.sample_id,
                "input": problem.prompt,
                "prediction": problem.canonical_solution,
                "reference": "hidden tests passed",
                "metric": "pass@1",
                "score": 1.0,
                "reason": None,
                "metadata": problem.metadata,
            },
            1,
            1,
        )
        return {"job_id": kwargs["job_id"], "total_samples": 1}

    monkeypatch.setattr(
        executor_module,
        "run_real_benchmark",
        lambda **kwargs: pytest.fail("HumanEval must not use the text runner"),
    )
    monkeypatch.setattr(
        executor_module,
        "prepare_dataset",
        lambda dataset: "fixture.gz",
        raising=False,
    )
    monkeypatch.setattr(
        executor_module,
        "load_humaneval_problems",
        lambda path: [problem],
        raising=False,
    )
    monkeypatch.setattr(executor_module, "run_humaneval_benchmark", fake_humaneval, raising=False)

    _evaluation_process("job_humaneval", asdict(request), event_queue)

    assert observed["job_id"] == "job_humaneval"
    assert observed["oracle_prediction"] == problem.canonical_solution
    assert observed["problems"] == [problem]
    assert event_queue.events[0]["sample"]["metadata"] == problem.metadata
    assert event_queue.events[-1]["type"] == "result"


def test_humaneval_infrastructure_error_keeps_stable_type_across_process_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HumanEval 沙箱基础设施错误跨进程后仍应保留可阻塞的稳定分类。"""
    request = replace(request_fixture(), dataset="hexagon-humaneval", sample_mode="all")
    child_queue = RecordingQueue()
    monkeypatch.setattr(executor_module, "prepare_dataset", lambda dataset: "fixture.gz")
    monkeypatch.setattr(executor_module, "load_humaneval_problems", lambda path: [])

    def fail_sandbox(**kwargs: object) -> dict[str, object]:
        """模拟镜像校验在任何候选评分前失败。"""
        raise SandboxInfrastructureError("image_untrusted")

    monkeypatch.setattr(executor_module, "run_humaneval_benchmark", fail_sandbox)

    _evaluation_process("job_humaneval", asdict(request), child_queue)
    parent_queue: Queue[dict[str, object]] = Queue()
    parent_queue.put(child_queue.events[-1])
    result, error = SubprocessEvaluationExecutor._read_event(
        parent_queue,
        result=None,
        error_message=None,
        on_progress=lambda completed, total: None,
        on_sample_result=None,
        on_trace=None,
    )

    assert result is None
    assert isinstance(error, TaskExecutionError)
    assert error.error_type == "image_untrusted"


def test_model_generation_error_keeps_stable_type_across_process_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型没有最终回答时应阻塞 Benchmark 节点而不是写入零分样本。"""
    child_queue = RecordingQueue()

    def fail_generation(**kwargs: object) -> dict[str, object]:
        """模拟 Ollama 思考耗尽预算后返回空最终答案。

        Raises:
            ModelGenerationError: 每次调用均报告稳定的不完整生成分类。
        """
        del kwargs
        raise ModelGenerationError("generation_incomplete", "没有可评分最终答案")

    monkeypatch.setattr(executor_module, "run_real_benchmark", fail_generation)
    _evaluation_process("job_empty", asdict(request_fixture()), child_queue)

    parent_queue: Queue[dict[str, object]] = Queue()
    parent_queue.put(child_queue.events[-1])
    result, error = SubprocessEvaluationExecutor._read_event(
        parent_queue,
        result=None,
        error_message=None,
        on_progress=lambda completed, total: None,
        on_sample_result=None,
        on_trace=None,
    )

    assert result is None
    assert isinstance(error, TaskExecutionError)
    assert error.error_type == "generation_incomplete"


class EmptyParentQueue:
    """模拟子进程崩溃时没有任何终态消息的父进程队列。"""

    def get(self, *, timeout: float) -> dict[str, object]:
        """始终表示超时期间没有事件到达。"""
        raise Empty

    def close(self) -> None:
        """提供执行器 finally 路径需要的无副作用关闭接口。"""


class ExitedProcess:
    """模拟启动后立即以非零状态退出的评测子进程。"""

    pid = 321
    exitcode = 7

    def start(self) -> None:
        """保持进程已经退出的固定状态。"""

    def is_alive(self) -> bool:
        """返回 false 表示子进程已不再运行。"""
        return False

    def join(self, *, timeout: float) -> None:
        """模拟立即完成的进程回收。"""

    def terminate(self) -> None:
        """记录接口兼容性；退出进程无需再次终止。"""


class ExitedProcessContext:
    """向执行器提供固定的空队列和已退出进程。"""

    def Queue(self) -> EmptyParentQueue:
        """返回不会产生任何消息的父进程队列。"""
        return EmptyParentQueue()

    def Process(self, **kwargs: object) -> ExitedProcess:
        """忽略构造参数并返回已退出进程。"""
        return ExitedProcess()


class LaggingResultQueue:
    """模拟进程退出后一拍才可见最终结果的跨进程队列。"""

    def __init__(self) -> None:
        """记录读取次数，使第一次读取稳定复现 feeder 延迟。"""
        self.read_count = 0

    def get(self, *, timeout: float) -> dict[str, object]:
        """第一次报告空队列，后续返回已由子进程写入的最终结果。"""
        self.read_count += 1
        if self.read_count == 1:
            raise Empty
        return {"type": "result", "result": {"job_id": "job_lagging_result"}}

    def close(self) -> None:
        """提供执行器 finally 路径需要的无副作用关闭接口。"""


class SuccessfulExitedProcess(ExitedProcess):
    """模拟已经正常退出、但队列 feeder 尚未暴露结果的子进程。"""

    exitcode = 0


class LaggingResultProcessContext:
    """向执行器提供延迟可见结果和已正常退出的进程。"""

    def __init__(self) -> None:
        """创建本次执行唯一的延迟队列，便于断言读取行为。"""
        self.queue = LaggingResultQueue()

    def Queue(self) -> LaggingResultQueue:
        """返回首次读取为空、随后提供结果的队列。"""
        return self.queue

    def Process(self, **kwargs: object) -> SuccessfulExitedProcess:
        """忽略构造参数并返回已经正常退出的固定进程。"""
        return SuccessfulExitedProcess()


class ZeroResourceSampler:
    """为执行器异常退出测试提供无外部依赖的零资源读数。"""

    def sample(self, process_id: int) -> ResourceUsage:
        """返回零读数，使测试只观察缺失终态消息行为。"""
        return ResourceUsage()


def test_executor_stops_when_process_exits_without_result() -> None:
    """子进程没有终态消息时执行器应快速失败，不能无限等待队列。"""
    executor = SubprocessEvaluationExecutor(resource_sampler=ZeroResourceSampler())
    executor._context = cast(object, ExitedProcessContext())
    cancel_event = Event()
    errors: list[Exception] = []

    def run_executor() -> None:
        """在线程中调用执行器，允许测试检测无限等待并安全释放。"""
        try:
            executor.execute(
                "job_crashed",
                request_fixture(),
                on_progress=lambda completed, total: None,
                on_resources=lambda usage: None,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            errors.append(exc)

    thread = Thread(target=run_executor)
    thread.start()
    thread.join(timeout=0.2)
    finished_without_cancel = not thread.is_alive()
    # 若实现错误地无限等待，设置取消信号回收测试线程，再对原始行为做断言。
    cancel_event.set()
    thread.join(timeout=1.0)

    assert finished_without_cancel is True
    assert len(errors) == 1
    assert isinstance(errors[0], TaskExecutionError)
    assert not isinstance(errors[0], TaskExecutionCanceled)
    assert "exit code 7" in str(errors[0])


def test_executor_drains_lagging_result_after_process_exits() -> None:
    """正常退出后的队列短暂为空时，执行器仍应读取随后可见的最终结果。"""
    executor = SubprocessEvaluationExecutor(resource_sampler=ZeroResourceSampler())
    context = LaggingResultProcessContext()
    executor._context = cast(object, context)

    result = executor.execute(
        "job_lagging_result",
        request_fixture(),
        on_progress=lambda completed, total: None,
        on_resources=lambda usage: None,
        cancel_event=Event(),
    )

    assert result == {"job_id": "job_lagging_result"}
    assert context.queue.read_count == 2
