"""提供持久化 FIFO 评测任务调度、查询和取消服务。"""

from __future__ import annotations

import fcntl
from collections.abc import Callable
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import BinaryIO, Protocol, cast

from evalhub.agent import AgentTraceEvent, TraceCallback
from evalhub.tasks.executor import (
    SubprocessEvaluationExecutor,
    TaskExecutionCanceled,
)
from evalhub.tasks.models import (
    EvaluationNode,
    EvaluationNodeEvent,
    EvaluationSamplePage,
    EvaluationTask,
    ResourceUsage,
    TaskRequest,
)
from evalhub.tasks.performance import ModelPerformanceReport, build_model_performance
from evalhub.tasks.repository import SQLiteTaskRepository, TaskNotFoundError, TaskStateError
from evalhub.tasks.runtime import PersistentWorkflowExecutor
from evalhub.tasks.workflow import build_agent_workflow, build_workflow


class TaskExecutor(Protocol):
    """描述任务服务可调用的隔离评测执行边界。"""

    def execute(
        self,
        task_id: str,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
    ) -> dict[str, object]:
        """执行一个任务并通过回调报告进度和资源，最终返回评测结果。"""


class AgentTaskExecutor(Protocol):
    """描述会额外上报标准化执行过程事件的 Agent 执行边界。"""

    def execute(
        self,
        task_id: str,
        request: TaskRequest,
        *,
        on_progress: Callable[[int, int], None],
        on_resources: Callable[[ResourceUsage], None],
        cancel_event: Event,
        on_trace: TraceCallback | None = None,
    ) -> dict[str, object]:
        """执行 Agent Benchmark，并通过回调上报进度、资源与白名单 Trace。"""


class TaskConflictError(RuntimeError):
    """表示调用方试图取消已经进入终态的任务。"""


class EvaluationTaskService:
    """使用单后台线程按 FIFO 顺序执行持久化评测任务。"""

    def __init__(
        self,
        repository: SQLiteTaskRepository,
        *,
        executor: TaskExecutor | None = None,
        agent_executor: AgentTaskExecutor | None = None,
    ) -> None:
        """注入仓储及模型、Agent 执行器，但不在构造阶段启动线程。"""
        self._repository = repository
        self._executor = executor or PersistentWorkflowExecutor(repository)
        # 旧调用方只注入一个执行器时仍沿用它；新调用方可单独替换 Agent 边界。
        self._agent_executor = agent_executor or (
            cast(AgentTaskExecutor, executor)
            if executor is not None
            else SubprocessEvaluationExecutor()
        )
        self._queue: Queue[str | None] = Queue()
        self._stop_event = Event()
        self._worker: Thread | None = None
        # 提交锁把数据库创建顺序与内存入队顺序绑定，HTTP 并发不能打乱 FIFO。
        self._submission_lock = Lock()
        # 活动任务与取消事件由同一把锁保护，关闭执行开始和取消之间的竞态窗口。
        self._active_lock = Lock()
        self._active_task_id: str | None = None
        self._active_cancel_event: Event | None = None
        self._worker_lock_file: BinaryIO | None = None

    def start(self) -> None:
        """恢复数据库任务并启动唯一后台 Worker。

        重复调用不会创建第二个线程，保证单 Worker 资源归属语义。
        """
        if self._worker is not None and self._worker.is_alive():
            return
        if self._worker is not None:
            # 停服后内存队列可能残留哨兵或旧任务；SQLite 才是重启时的唯一事实来源。
            self._queue = Queue()
        self._acquire_worker_lock()
        try:
            # 模型任务按节点检查点恢复；没有节点的旧任务仍保持原有明确失败语义。
            self._repository.recover_running_nodes()
            for task in self._repository.list_resumable():
                if task.status == "running" and not self._repository.list_nodes(task.id):
                    self._repository.mark_failed(task.id, "服务重启导致评测中断")
                    continue
                if task.status == "failed":
                    self._repository.reopen_for_retry(task.id)
                self._queue.put(task.id)
            self._stop_event.clear()
            self._worker = Thread(target=self._worker_loop, name="evalhub-task-worker", daemon=True)
            self._worker.start()
        except Exception:
            self._release_worker_lock()
            raise

    def stop(self) -> None:
        """停止后台 Worker，并通知当前执行器回收活动子进程。"""
        self._stop_event.set()
        with self._active_lock:
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
        # 哨兵唤醒正在等待新任务的线程，有限等待避免服务关闭永久阻塞。
        self._queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            if self._worker.is_alive():
                return
        self._release_worker_lock()

    def _acquire_worker_lock(self) -> None:
        """为当前 SQLite 库获取跨进程独占 Worker 锁。

        锁由文件描述符生命周期持有，进程异常退出时操作系统会自动释放，不需要维护
        可能残留的 PID 文件。

        Raises:
            RuntimeError: 同一任务库已经由另一个服务实例持有 Worker 锁。
            OSError: 锁文件无法创建或操作系统拒绝执行文件锁操作。
        """
        if self._worker_lock_file is not None:
            return
        # 锁文件紧邻数据库，确保不同端口或启动目录最终竞争同一个任务库所有权。
        lock_path = self._repository.database_path.with_suffix(
            f"{self._repository.database_path.suffix}.worker.lock"
        )
        lock_file = lock_path.open("a+b")
        try:
            # 非阻塞获取让重复启动立即失败，不能等待旧服务退出后偷偷接管活动任务。
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise RuntimeError(
                f"EvalHub worker is already running for database: {self._repository.database_path}"
            ) from exc
        self._worker_lock_file = lock_file

    def _release_worker_lock(self) -> None:
        """在 Worker 确认退出后释放当前服务持有的数据库锁。

        尚未获取锁或已经释放时直接返回，使启动失败和重复关闭共享同一清理路径。
        """
        if self._worker_lock_file is None:
            return
        # 先解锁再关闭文件，避免后续同进程服务依赖隐式 close 语义获取所有权。
        fcntl.flock(self._worker_lock_file.fileno(), fcntl.LOCK_UN)
        self._worker_lock_file.close()
        self._worker_lock_file = None

    def submit(self, request: TaskRequest) -> EvaluationTask:
        """先持久化一个排队任务，再把其标识放入 FIFO 队列。"""
        # 创建与入队作为同一临界区，使数据库 rowid 顺序和 Worker 消费顺序一致。
        with self._submission_lock:
            if request.evaluation_type == "agent":
                task = self._repository.create_with_nodes(request, build_agent_workflow(request))
            else:
                task = self._repository.create_with_nodes(request, build_workflow(request))
            self._queue.put(task.id)
        return task

    def list(self) -> list[EvaluationTask]:
        """返回按创建时间倒序排列的轻量任务快照。"""
        return self._repository.list()

    def model_performance(self, scope: str | None = None) -> ModelPerformanceReport:
        """按同一 Benchmark 或 Suite 聚合模型历史最好成绩和趋势。

        Args:
            scope: 可选的 `benchmark:<id>` 或 `suite:<id>` 稳定范围键。

        Returns:
            已排除 Agent 和无分任务的模型成绩聚合报告。

        Raises:
            ValueError: 显式请求的范围不存在有效模型成绩。
        """
        tasks = self._repository.list_scored()
        return build_model_performance(tasks, scope)

    def get(self, task_id: str) -> EvaluationTask:
        """按稳定标识返回包含完整结果的任务详情。"""
        return self._repository.get(task_id)

    def list_nodes(self, task_id: str) -> list[EvaluationNode]:
        """返回指定模型评测任务的持久化节点列表。"""
        return self._repository.list_nodes(task_id)

    def get_node(self, task_id: str, node_id: str) -> EvaluationNode:
        """在任务边界内读取节点，避免跨任务标识误取数据。"""
        self._repository.get(task_id)
        node = self._repository.get_node(node_id)
        if node.task_id != task_id:
            raise TaskNotFoundError(f"node not found: {node_id}")
        return node

    def list_node_events(self, task_id: str, node_id: str) -> list[EvaluationNodeEvent]:
        """返回指定任务节点的追加式审计事件。"""
        self.get_node(task_id, node_id)
        return self._repository.list_node_events(node_id)

    def list_node_samples(
        self,
        task_id: str,
        node_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> EvaluationSamplePage:
        """分页读取指定任务节点的样本检查点。"""
        self.get_node(task_id, node_id)
        return self._repository.list_samples(
            node_id,
            limit=limit,
            cursor=cursor,
            status=status,
        )

    def retry_node(self, task_id: str, node_id: str) -> EvaluationNode:
        """重置失败节点、重开顶层任务并按原 FIFO 边界重新入队。"""
        with self._submission_lock:
            task = self._repository.get(task_id)
            if task.status != "failed":
                raise TaskConflictError(f"task is already {task.status}")
            try:
                node = self._repository.retry_node(task_id, node_id)
            except TaskStateError as exc:
                raise TaskConflictError(str(exc)) from exc
            self._repository.reopen_for_retry(task_id)
            self._queue.put(task_id)
        return node

    def cancel(self, task_id: str) -> EvaluationTask:
        """取消排队或运行任务，并拒绝改写任何终态。

        Raises:
            TaskConflictError: 任务已经成功、失败或取消。
        """
        with self._active_lock:
            task = self._repository.get(task_id)
            if task.status in {"success", "failed", "canceled"}:
                raise TaskConflictError(f"task is already {task.status}")
            # 活动任务先发取消信号，再写终态；排队任务只需写库，出队时会被跳过。
            if task.status == "running" and self._active_task_id == task_id:
                if self._active_cancel_event is not None:
                    self._active_cancel_event.set()
            self._repository.cancel_nodes(task_id)
            return self._repository.mark_canceled(task_id)

    def _worker_loop(self) -> None:
        """持续从 FIFO 队列取任务，直到服务收到停止信号。"""
        while not self._stop_event.is_set():
            try:
                task_id = self._queue.get(timeout=0.2)
            except Empty:
                continue
            if task_id is None:
                return
            # 单线程串行调用保证任何时刻最多存在一个隔离评测进程。
            self._run_task(task_id)

    def _run_task(self, task_id: str) -> None:
        """执行一个仍在排队的任务并持久化其所有可观察状态。"""
        cancel_event = Event()
        with self._active_lock:
            task = self._repository.get(task_id)
            if task.status not in {"pending", "running"}:
                return
            running_task = (
                self._repository.mark_running(task_id) if task.status == "pending" else task
            )
            self._active_task_id = task_id
            self._active_cancel_event = cancel_event

        # 新 Agent 任务始终只有一个节点；无节点的旧数据继续走兼容执行路径。
        agent_node: EvaluationNode | None = None

        def persist_progress(completed: int, total: int) -> None:
            """在任务仍活动时把执行器进度事件写入 SQLite。"""
            if not cancel_event.is_set():
                self._repository.update_progress(task_id, completed=completed, total=total)
                if agent_node is not None:
                    self._repository.update_node_progress(
                        agent_node.id,
                        completed=completed,
                        total=total,
                    )

        def persist_resources(usage: ResourceUsage) -> None:
            """在任务仍活动时把资源快照和峰值写入 SQLite。"""
            if not cancel_event.is_set():
                self._repository.update_resources(task_id, usage)

        def persist_trace(event: AgentTraceEvent) -> None:
            """把执行器白名单 Trace 追加到当前 Agent 节点审计日志。"""
            if cancel_event.is_set() or agent_node is None:
                return
            self._repository.append_node_event(
                agent_node.id,
                event_type=event["event_type"],
                actor=event["actor"],
                message=event["message"],
                payload=event["payload"],
            )

        try:
            if running_task.request.evaluation_type == "agent":
                nodes = self._repository.list_nodes(task_id)
                if nodes:
                    agent_node = self._repository.start_node(nodes[0].id)
                result = self._agent_executor.execute(
                    task_id,
                    running_task.request,
                    on_progress=persist_progress,
                    on_resources=persist_resources,
                    cancel_event=cancel_event,
                    on_trace=persist_trace,
                )
            else:
                result = self._executor.execute(
                    task_id,
                    running_task.request,
                    on_progress=persist_progress,
                    on_resources=persist_resources,
                    cancel_event=cancel_event,
                )
            # 取消可能与最后结果同时到达；已取消终态优先，绝不能被成功覆盖。
            if not cancel_event.is_set():
                if agent_node is not None:
                    self._repository.complete_node(agent_node.id, result)
                self._repository.mark_success(task_id, result)
            else:
                self._persist_interruption(task_id, cancel_event)
        except TaskExecutionCanceled:
            self._fail_interrupted_agent_node(agent_node)
            self._persist_interruption(task_id, cancel_event)
        except Exception as exc:
            # 只把仍处于运行态的异常任务标失败，保留并发取消已经写入的终态。
            self._fail_running_agent_node(agent_node, exc)
            if self._repository.get(task_id).status == "running":
                self._repository.mark_failed(task_id, str(exc))
        finally:
            with self._active_lock:
                self._active_task_id = None
                self._active_cancel_event = None

    def _fail_running_agent_node(
        self,
        node: EvaluationNode | None,
        exc: Exception,
    ) -> None:
        """把仍在运行的 Agent 节点转换为带异常分类的失败终态。"""
        if node is None:
            return
        current = self._repository.get_node(node.id)
        if current.status == "running":
            self._repository.fail_node(node.id, type(exc).__name__, str(exc))

    def _fail_interrupted_agent_node(self, node: EvaluationNode | None) -> None:
        """服务关闭时标记 Agent 节点失败，并保留用户取消已经写入的终态。"""
        if node is None or not self._stop_event.is_set():
            return
        current = self._repository.get_node(node.id)
        if current.status == "running":
            self._repository.fail_node(node.id, "service_stopped", "服务停止导致评测中断")

    def _persist_interruption(self, task_id: str, cancel_event: Event) -> None:
        """按中断来源保存失败状态，并让公开取消操作独占用户取消终态。

        Args:
            task_id: 被中断的活动任务标识。
            cancel_event: 执行器收到的本次任务取消信号。
        """
        task = self._repository.get(task_id)
        if task.status != "running":
            return
        if self._stop_event.is_set():
            # 模型节点和样本已有检查点，停服后应重新排队；无节点旧任务保留失败语义。
            if self._repository.list_nodes(task_id):
                self._repository.requeue_for_resume(task_id)
            else:
                self._repository.mark_failed(task_id, "服务停止导致评测中断")
        elif not cancel_event.is_set():
            # 执行器自行报告取消但服务从未发信号，按异常中断而非用户操作记录。
            self._repository.mark_failed(task_id, "评测执行意外中断")
