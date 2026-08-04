"""在独立子进程执行真实 Benchmark，并向任务服务上报事件。"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from dataclasses import asdict
from queue import Empty
from threading import Event
from time import monotonic
from typing import Protocol

from evalhub.agent.codex import AgentTraceEvent, TraceCallback
from evalhub.benchmarks import (
    DockerHumanEvalSandbox,
    SandboxInfrastructureError,
    load_humaneval_problems,
    run_humaneval_benchmark,
)
from evalhub.benchmarks.coding_mini import run_codex_agent_benchmark
from evalhub.cli import build_model_adapter, run_real_benchmark
from evalhub.datasets import prepare_dataset
from evalhub.domain import EvaluationSampleResult
from evalhub.tasks.models import ResourceUsage, TaskRequest
from evalhub.tasks.resources import ProcessResourceSampler

_PROCESS_EXIT_DRAIN_SECONDS = 0.1


class TaskExecutionCanceled(RuntimeError):
    """表示任务服务已请求终止当前评测子进程。"""


class TaskExecutionError(RuntimeError):
    """表示评测子进程以异常或缺少结果的方式结束。"""

    def __init__(self, message: str, *, error_type: str | None = None) -> None:
        """保存安全错误消息及可选的稳定基础设施分类。

        Args:
            message: 可向任务节点展示的脱敏错误消息。
            error_type: 仅由可信子进程边界写入的稳定错误代码。
        """
        super().__init__(message)
        self.error_type = error_type


class MessageQueue(Protocol):
    """描述子进程事件传输所需的最小队列接口。"""

    def put(self, value: dict[str, object]) -> None:
        """把一个 JSON 兼容事件写入父子进程队列。"""


def _evaluation_process(
    task_id: str,
    request_payload: dict[str, object],
    event_queue: MessageQueue,
    skip_sample_ids: tuple[str, ...] = (),
) -> None:
    """在子进程中执行评测并发送进度、结果或错误事件。

    Args:
        task_id: 调度层预先持久化的任务标识。
        request_payload: 可重建 ``TaskRequest`` 的纯字典。
        event_queue: 向父进程发送事件的跨进程队列。
        skip_sample_ids: 模型评测恢复时已经完成推理和评分、无需重复运行的样本标识。
    """
    request = TaskRequest(**request_payload)

    def report_progress(completed: int, total: int) -> None:
        """把同步 Runner 回调转换为跨进程进度事件。"""
        event_queue.put({"type": "progress", "completed": completed, "total": total})

    def report_sample(
        sample: EvaluationSampleResult | dict[str, object],
        completed: int,
        total: int,
    ) -> None:
        """把文本领域结果或 HumanEval 脱敏结果转换为统一跨进程事件。

        Args:
            sample: 文本 Runner 领域实体，或 HumanEval Runner 已脱敏的结果字典。
            completed: 包含恢复检查点在内的当前完成数量。
            total: 当前 Benchmark 固定样本总数。
        """
        if isinstance(sample, EvaluationSampleResult):
            payload: dict[str, object] = {
                "sample_id": sample.sample_id,
                "input": sample.input,
                "prediction": sample.prediction,
                "reference": sample.reference,
                "metric": sample.metric,
                "score": sample.score,
                "reason": sample.reason,
                "metadata": sample.metadata,
            }
        else:
            # HumanEval 已在专用 Runner 脱敏，父进程只需沿用同一结果字典协议。
            payload = dict(sample)
            payload.setdefault("metadata", {})
        event_queue.put(
            {
                "type": "sample_result",
                "completed": completed,
                "total": total,
                "sample": payload,
            }
        )

    def report_trace(event: AgentTraceEvent) -> None:
        """把标准化 Agent 外部动作转换为跨进程 Trace 事件。"""
        event_queue.put({"type": "trace_event", "event": event})

    try:
        # Agent 样本由难度目录筛选；不再复用模型评测的条数限制语义。
        if request.evaluation_type == "agent":
            result = run_codex_agent_benchmark(
                job_id=task_id,
                model=request.model,
                base_url=request.base_url,
                difficulty=request.agent_difficulty or "all",
                on_progress=report_progress,
                on_trace=report_trace,
            )
        else:
            # 模型评测继续沿用已有 all、quick 与自定义条数规则。
            if request.sample_mode == "all":
                limit = None
            elif request.sample_mode == "quick":
                limit = 5
            else:
                limit = request.limit
            if request.dataset == "hexagon-humaneval":
                path = prepare_dataset(request.dataset)
                problems = load_humaneval_problems(path)
                if limit is not None:
                    problems = problems[:limit]
                # Oracle 只回放官方实现；模型仍只看到公开英文 prompt，隐藏测试进入 Docker。
                adapter = build_model_adapter(
                    request.adapter,
                    model=request.model,
                    base_url=request.base_url,
                    oracle_responses={
                        problem.prompt: problem.canonical_solution for problem in problems
                    },
                )
                result = run_humaneval_benchmark(
                    job_id=task_id,
                    adapter=adapter,
                    problems=problems,
                    sandbox=DockerHumanEvalSandbox(),
                    skip_sample_ids=frozenset(skip_sample_ids),
                    on_progress=report_progress,
                    on_sample_result=report_sample,
                    generation_config=request.generation_config,
                )
            else:
                result = run_real_benchmark(
                    dataset=request.dataset,
                    adapter_type=request.adapter,
                    model=request.model,
                    base_url=request.base_url,
                    limit=limit,
                    subject=request.subject,
                    job_id=task_id,
                    on_progress=report_progress,
                    skip_sample_ids=frozenset(skip_sample_ids),
                    on_sample_result=report_sample,
                    generation_config=request.generation_config,
                    evaluator_type=request.evaluator_type,
                )
        event_queue.put({"type": "result", "result": result})
    except SandboxInfrastructureError as exc:
        # 沙箱基础设施错误必须跨进程保留分类，父流程据此阻塞而不是记录模型失败。
        event_queue.put({"type": "error", "message": str(exc), "error_type": str(exc)})
    except Exception as exc:
        # 子进程边界只发送安全字符串，不尝试跨进程序列化任意异常对象和堆栈。
        event_queue.put({"type": "error", "message": str(exc)})


class SubprocessEvaluationExecutor:
    """管理单次隔离评测进程、事件传递、资源采样和取消。"""

    def __init__(
        self,
        *,
        resource_sampler: ProcessResourceSampler | None = None,
        sample_interval: float = 1.0,
    ) -> None:
        """配置资源采样器与采样周期。

        Args:
            resource_sampler: 读取评测进程树资源的可替换采样器。
            sample_interval: 连续资源快照之间的最小秒数。
        """
        self._resource_sampler = resource_sampler
        self._sample_interval = sample_interval
        self._context = multiprocessing.get_context("spawn")

    def execute(
        self,
        task_id: str,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
        skip_sample_ids: set[str] | frozenset[str] = frozenset(),
        on_sample_result: Callable[[dict[str, object], int, int], None] | None = None,
        on_trace: TraceCallback | None = None,
    ) -> dict[str, object]:
        """运行一个评测子进程直到成功、失败或收到取消信号。

        Args:
            task_id: 当前评测任务标识。
            request: 已校验的评测请求。
            on_progress: 持久化样本进度的回调。
            on_resources: 持久化资源快照的回调。
            cancel_event: 调度层发送取消信号的线程事件。
            skip_sample_ids: 恢复执行时无需重复计算的样本标识。
            on_sample_result: 可选的单样本结果回调。
            on_trace: 可选的 Agent 白名单过程事件回调。

        Returns:
            子进程生成的最终评测结果字典。

        Raises:
            TaskExecutionCanceled: 调度层请求取消任务。
            TaskExecutionError: 子进程报告异常或未返回结果。
        """
        event_queue = self._context.Queue()
        process = self._context.Process(
            target=_evaluation_process,
            args=(task_id, asdict(request), event_queue, tuple(sorted(skip_sample_ids))),
            daemon=True,
        )
        process.start()
        # Ollama 推理在独立服务进程执行，生产采样器需纳入本机 CPU；测试注入保持原接口。
        resource_sampler = self._resource_sampler or ProcessResourceSampler(
            include_system_cpu=request.adapter == "ollama"
        )
        next_sample_at = monotonic()
        result: dict[str, object] | None = None
        error_message: TaskExecutionError | None = None
        process_exited_at: float | None = None

        try:
            while True:
                if cancel_event.is_set():
                    # 取消优先于新事件处理，确保 UI 操作能尽快回收模型请求进程。
                    self._terminate(process)
                    raise TaskExecutionCanceled("evaluation canceled")
                result, error_message = self._read_event(
                    event_queue,
                    result=result,
                    error_message=error_message,
                    on_progress=on_progress,
                    on_sample_result=on_sample_result,
                    on_trace=on_trace,
                )
                if error_message is not None or result is not None:
                    break
                # multiprocessing.Queue 的 feeder 可能晚于进程退出状态暴露最后几条消息。
                if not process.is_alive():
                    if process_exited_at is None:
                        process_exited_at = monotonic()
                    elif monotonic() - process_exited_at >= _PROCESS_EXIT_DRAIN_SECONDS:
                        break
                    continue

                now = monotonic()
                if now >= next_sample_at and process.pid is not None:
                    # 资源采样与进度消息独立，慢模型响应期间仍会持续刷新遥测。
                    on_resources(resource_sampler.sample(process.pid))
                    next_sample_at = now + self._sample_interval

            process.join(timeout=1.0)
            if error_message is not None:
                raise error_message
            if result is None:
                raise TaskExecutionError(
                    f"evaluation process exited without result (exit code {process.exitcode})"
                )
            return result
        finally:
            # 任一异常路径都确保子进程和队列被回收，避免服务长期运行积累句柄。
            if process.is_alive():
                self._terminate(process)
            event_queue.close()

    @staticmethod
    def _read_event(
        event_queue: object,
        *,
        result: dict[str, object] | None,
        error_message: TaskExecutionError | None,
        on_progress: Callable[[int, int], None],
        on_sample_result: Callable[[dict[str, object], int, int], None] | None,
        on_trace: TraceCallback | None,
    ) -> tuple[dict[str, object] | None, TaskExecutionError | None]:
        """读取一个子进程事件并更新进度或终态暂存值。"""
        try:
            message = event_queue.get(timeout=0.05)
        except Empty:
            return result, error_message
        event_type = message.get("type")
        if event_type == "progress":
            on_progress(int(message["completed"]), int(message["total"]))
        elif event_type == "sample_result" and on_sample_result is not None:
            on_sample_result(
                dict(message["sample"]),
                int(message["completed"]),
                int(message["total"]),
            )
        elif event_type == "trace_event" and on_trace is not None:
            on_trace(dict(message["event"]))
        elif event_type == "result":
            result = message["result"]
        elif event_type == "error":
            error_type = message.get("error_type")
            error_message = TaskExecutionError(
                str(message["message"]),
                error_type=str(error_type) if error_type is not None else None,
            )
        return result, error_message

    @staticmethod
    def _terminate(process: object) -> None:
        """终止并等待仍存活的评测子进程释放系统资源。"""
        if process.is_alive():
            process.terminate()
        process.join(timeout=2.0)
