"""管理本机 Ollama 模型下载任务并归一化流式进度。"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ACTIVE_PULL_STATUSES = {"pending", "pulling", "verifying"}
MODEL_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$"
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class OllamaPullError(RuntimeError):
    """表示 Ollama 流式响应明确报告的下载失败。"""


@dataclass
class OllamaPullTask:
    """保存一个模型下载的公开状态和内部同步资源。"""

    model: str
    base_url: str
    status: str = "pending"
    message: str = "等待下载"
    completed_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    response: Any | None = field(default=None, repr=False)
    last_completed_bytes: int | None = field(default=None, repr=False)
    last_progress_at: float | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, object]:
        """返回不含线程和响应对象的 JSON 兼容快照。

        Returns:
            可直接作为本地 HTTP API 响应序列化的任务状态字典。
        """
        return {
            "model": self.model,
            "status": self.status,
            "message": self.message,
            "completed_bytes": self.completed_bytes,
            "total_bytes": self.total_bytes,
            "speed_bytes_per_second": self.speed_bytes_per_second,
            "eta_seconds": self.eta_seconds,
            "error": self.error,
        }


class OllamaPullManager:
    """串行执行本机模型下载并提供线程安全任务快照。"""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化线程安全的模型下载管理器。

        Args:
            opener: 创建 Ollama HTTP 流式响应的可替换调用器，测试使用轻量 Fake。
            clock: 计算下载速率和剩余时间的单调时钟。
        """
        self._opener = opener
        self._clock = clock
        self._tasks: dict[str, OllamaPullTask] = {}
        self._state_lock = threading.RLock()
        self._execution_lock = threading.Lock()

    def start(self, model: str, base_url: str) -> dict[str, object]:
        """创建模型下载；相同模型的活动任务保持幂等。

        Args:
            model: 满足 Ollama 标签格式的目标模型名称。
            base_url: 不含凭据、查询或额外路径的本机 HTTP 回环地址。

        Returns:
            新建任务或现有活动任务的 JSON 兼容快照。

        Raises:
            ValueError: 模型名称或 Ollama 地址不符合本地下载安全约束。
        """
        # 所有外部输入在线程启动前完成校验，避免后台错误丢失在 HTTP 响应之后。
        normalized_model = _validate_model_name(model)
        normalized_base_url = _validate_loopback_base_url(base_url)
        with self._state_lock:
            existing = self._tasks.get(normalized_model)
            if existing is not None and existing.status in ACTIVE_PULL_STATUSES:
                return existing.as_dict()
            task = OllamaPullTask(model=normalized_model, base_url=normalized_base_url)
            self._tasks[normalized_model] = task

        thread = threading.Thread(
            target=self._run,
            args=(task,),
            name=f"ollama-pull-{normalized_model}",
            daemon=True,
        )
        # 守护线程不阻止本地服务退出；任务状态仍由管理器内存保存供页面轮询。
        thread.start()
        return task.as_dict()

    def get(self, model: str) -> dict[str, object] | None:
        """读取模型任务快照。

        Args:
            model: 要恢复状态的 Ollama 模型标签。

        Returns:
            任务的 JSON 兼容快照；当前进程没有该模型记录时返回 ``None``。
        """
        with self._state_lock:
            task = self._tasks.get(model)
            return task.as_dict() if task is not None else None

    def cancel(self, model: str) -> dict[str, object] | None:
        """尽力取消活动任务并关闭当前 Ollama 流式响应。

        Args:
            model: 要取消的 Ollama 模型标签。

        Returns:
            取消后的任务快照；任务不存在时返回 ``None``。

        Side Effects:
            设置线程取消事件，并在响应支持关闭时中断阻塞读取。
        """
        with self._state_lock:
            task = self._tasks.get(model)
            if task is None:
                return None
            if task.status not in ACTIVE_PULL_STATUSES:
                return task.as_dict()
            task.cancel_event.set()
            task.status = "canceled"
            task.message = "下载已取消"
            task.error = None
            response = task.response
            snapshot = task.as_dict()
        # 响应关闭放在状态锁外，避免慢速 I/O 阻塞其他状态查询。
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        return snapshot

    def _run(self, task: OllamaPullTask) -> None:
        """在全局串行锁内消费单个 Ollama Pull 响应。

        Args:
            task: 已登记、由当前后台线程推进的下载任务。

        Side Effects:
            向 Ollama 发起流式请求，并持续更新任务进度或终态。
        """
        with self._execution_lock:
            if task.cancel_event.is_set():
                self._finish_canceled(task)
                return
            self._set_running(task)
            request = Request(
                f"{task.base_url}/api/pull",
                data=json.dumps({"model": task.model, "stream": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # Ollama 逐行返回 NDJSON；每条事件都在进入共享状态前完成解码和归一化。
            try:
                with self._opener(request, timeout=30) as response:
                    with self._state_lock:
                        task.response = response
                    for raw_line in response:
                        if task.cancel_event.is_set():
                            self._finish_canceled(task)
                            return
                        if not raw_line.strip():
                            continue
                        event = json.loads(raw_line.decode("utf-8"))
                        self._apply_event(task, event)
                        if task.status in {"success", "failed", "canceled"}:
                            return
            except Exception as exc:
                # 后台线程必须把所有传输、解码和 Fake 边界异常收敛为可轮询终态。
                if task.cancel_event.is_set():
                    self._finish_canceled(task)
                else:
                    self._finish_failed(task, str(exc))
            finally:
                with self._state_lock:
                    task.response = None

    def _set_running(self, task: OllamaPullTask) -> None:
        """把已取得串行执行权的任务标记为正在连接 Ollama。

        Args:
            task: 即将发起 HTTP 请求的下载任务。
        """
        with self._state_lock:
            task.status = "pulling"
            task.message = "正在连接 Ollama"
            task.error = None

    def _apply_event(self, task: OllamaPullTask, event: dict[str, object]) -> None:
        """把一条 Ollama NDJSON 事件合并进下载任务。

        Args:
            task: 正在推进的下载任务。
            event: 已解码的 Ollama 流式响应对象。

        Raises:
            OllamaPullError: 事件显式包含 Ollama 错误信息。
        """
        error = event.get("error")
        if error:
            raise OllamaPullError(str(error))

        message = str(event.get("status") or "正在下载")
        completed = _optional_non_negative_int(event.get("completed"))
        total = _optional_non_negative_int(event.get("total"))
        with self._state_lock:
            task.message = message
            if "verifying" in message.lower():
                task.status = "verifying"
            elif message.lower() == "success":
                task.status = "success"
                task.message = "模型下载完成"
            else:
                task.status = "pulling"

            # 有字节进度时同步计算速率和 ETA；只有总量时仍保留容量用于后续事件。
            if completed is not None:
                self._update_progress(task, completed, total)
            elif total is not None:
                task.total_bytes = total

    def _update_progress(
        self, task: OllamaPullTask, completed: int, total: int | None
    ) -> None:
        """基于相邻进度事件计算瞬时速率和剩余时间。

        Args:
            task: 当前下载任务；调用方已持有状态锁。
            completed: Ollama 报告的累计完成字节数。
            total: Ollama 报告的总字节数，部分事件可能缺失。
        """
        now = self._clock()
        if task.last_completed_bytes is not None and task.last_progress_at is not None:
            byte_delta = completed - task.last_completed_bytes
            time_delta = now - task.last_progress_at
            if byte_delta > 0 and time_delta > 0:
                speed = byte_delta / time_delta
                task.speed_bytes_per_second = speed
                if total is not None and total > completed:
                    task.eta_seconds = math.ceil((total - completed) / speed)
                else:
                    task.eta_seconds = 0
        # 无论能否计算速度都记录最新累计值，供下一个事件建立可靠差值基线。
        task.completed_bytes = completed
        if total is not None:
            task.total_bytes = total
        task.last_completed_bytes = completed
        task.last_progress_at = now

    def _finish_failed(self, task: OllamaPullTask, error: str) -> None:
        """把后台异常转换为页面可恢复的失败终态。

        Args:
            task: 发生错误的下载任务。
            error: 保留给本地用户诊断的异常消息。
        """
        with self._state_lock:
            task.status = "failed"
            task.message = "模型下载失败"
            task.error = error

    def _finish_canceled(self, task: OllamaPullTask) -> None:
        """把收到取消信号的任务归一化为无错误的取消终态。

        Args:
            task: 用户已请求取消的下载任务。
        """
        with self._state_lock:
            task.status = "canceled"
            task.message = "下载已取消"
            task.error = None


def _validate_model_name(model: str) -> str:
    """校验模型标签并返回原始规范值。

    Args:
        model: 用户从推荐列表或文本边界提交的模型标签。

    Returns:
        通过格式校验的原字符串。

    Raises:
        ValueError: 标签包含空白、协议字符或不支持的路径结构。
    """
    if not MODEL_NAME_PATTERN.fullmatch(model):
        raise ValueError("invalid Ollama model name")
    return model


def _validate_loopback_base_url(base_url: str) -> str:
    """只接受没有凭据和额外路径的本机 HTTP Ollama 地址。

    Args:
        base_url: 用户配置的 Ollama 服务根地址。

    Returns:
        移除末尾斜杠后的安全本机地址。

    Raises:
        ValueError: 地址不是 HTTP 回环主机或携带凭据、路径、查询和片段。
    """
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Ollama pull base URL must be an HTTP loopback address")
    return base_url.rstrip("/")


def _optional_non_negative_int(value: object) -> int | None:
    """把合法非负整数进度值归一化，忽略布尔值和其他类型。

    Args:
        value: Ollama 动态 JSON 事件中的容量字段。

    Returns:
        可用于进度计算的非负整数；字段非法或缺失时返回 ``None``。
    """
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
