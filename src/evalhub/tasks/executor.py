"""在独立子进程执行真实 Benchmark，并向任务服务上报事件。"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from dataclasses import asdict
from queue import Empty
from threading import Event
from time import monotonic
from typing import Protocol

from evalhub.benchmarks.coding_mini import run_codex_agent_benchmark
from evalhub.cli import run_real_benchmark
from evalhub.domain import EvaluationSampleResult
from evalhub.tasks.models import ResourceUsage, TaskRequest
from evalhub.tasks.resources import ProcessResourceSampler


class TaskExecutionCanceled(RuntimeError):
    """表示任务服务已请求终止当前评测子进程。"""


class TaskExecutionError(RuntimeError):
    """表示评测子进程以异常或缺少结果的方式结束。"""


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
        skip_sample_ids: 模型评测恢复时已经完成、无需重复运行的样本标识。
    """
    request = TaskRequest(**request_payload)

    def report_progress(completed: int, total: int) -> None:
        """把同步 Runner 回调转换为跨进程进度事件。"""
        event_queue.put({"type": "progress", "completed": completed, "total": total})

    def report_sample(
        sample: EvaluationSampleResult,
        completed: int,
        total: int,
    ) -> None:
        """把领域样本结果转换为可跨进程传输的纯字典事件。"""
        event_queue.put(
            {
                "type": "sample_result",
                "completed": completed,
                "total": total,
                "sample": {
                    "sample_id": sample.sample_id,
                    "input": sample.input,
                    "prediction": sample.prediction,
                    "reference": sample.reference,
                    "metric": sample.metric,
                    "score": sample.score,
                    "reason": sample.reason,
                },
            }
        )

    try:
        # 两类评测的 quick 规模不同；显式分派避免把 Agent 语义塞进模型 Runner。
        if request.sample_mode == "all":
            limit = None
        elif request.sample_mode == "quick":
            limit = 3 if request.evaluation_type == "agent" else 5
        else:
            limit = request.limit

        # Agent MVP 固定 Coding Mini 与 Codex 壳；普通评测完整保留原有恢复回调。
        if request.evaluation_type == "agent":
            result = run_codex_agent_benchmark(
                job_id=task_id,
                model=request.model,
                base_url=request.base_url,
                limit=limit,
                on_progress=report_progress,
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
            )
        event_queue.put({"type": "result", "result": result})
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
    ) -> dict[str, object]:
        """运行一个评测子进程直到成功、失败或收到取消信号。

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
        error_message: str | None = None

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
                )
                if error_message is not None or result is not None:
                    break
                # 已退出进程在一次队列等待后仍没有终态消息，必须转入明确失败而非空转。
                if not process.is_alive():
                    break

                now = monotonic()
                if now >= next_sample_at and process.pid is not None:
                    # 资源采样与进度消息独立，慢模型响应期间仍会持续刷新遥测。
                    on_resources(resource_sampler.sample(process.pid))
                    next_sample_at = now + self._sample_interval

            process.join(timeout=1.0)
            if error_message is not None:
                raise TaskExecutionError(error_message)
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
        error_message: str | None,
        on_progress: Callable[[int, int], None],
        on_sample_result: Callable[[dict[str, object], int, int], None] | None,
    ) -> tuple[dict[str, object] | None, str | None]:
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
        elif event_type == "result":
            result = message["result"]
        elif event_type == "error":
            error_message = str(message["message"])
        return result, error_message

    @staticmethod
    def _terminate(process: object) -> None:
        """终止并等待仍存活的评测子进程释放系统资源。"""
        if process.is_alive():
            process.terminate()
        process.join(timeout=2.0)
