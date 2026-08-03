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
        """返回不含线程和响应对象的 JSON 兼容快照。"""
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
        self._opener = opener
        self._clock = clock
        self._tasks: dict[str, OllamaPullTask] = {}
        self._state_lock = threading.RLock()
        self._execution_lock = threading.Lock()

    def start(self, model: str, base_url: str) -> dict[str, object]:
        """创建模型下载；相同模型的活动任务保持幂等。"""
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
        thread.start()
        return task.as_dict()

    def get(self, model: str) -> dict[str, object] | None:
        """读取模型任务快照；没有记录时返回 ``None``。"""
        with self._state_lock:
            task = self._tasks.get(model)
            return task.as_dict() if task is not None else None

    def cancel(self, model: str) -> dict[str, object] | None:
        """尽力取消活动任务并关闭当前 Ollama 流式响应。"""
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
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        return snapshot

    def _run(self, task: OllamaPullTask) -> None:
        """在全局串行锁内消费单个 Ollama Pull 响应。"""
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
                if task.cancel_event.is_set():
                    self._finish_canceled(task)
                else:
                    self._finish_failed(task, str(exc))
            finally:
                with self._state_lock:
                    task.response = None

    def _set_running(self, task: OllamaPullTask) -> None:
        with self._state_lock:
            task.status = "pulling"
            task.message = "正在连接 Ollama"
            task.error = None

    def _apply_event(self, task: OllamaPullTask, event: dict[str, object]) -> None:
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

            if completed is not None:
                self._update_progress(task, completed, total)
            elif total is not None:
                task.total_bytes = total

    def _update_progress(
        self, task: OllamaPullTask, completed: int, total: int | None
    ) -> None:
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
        task.completed_bytes = completed
        if total is not None:
            task.total_bytes = total
        task.last_completed_bytes = completed
        task.last_progress_at = now

    def _finish_failed(self, task: OllamaPullTask, error: str) -> None:
        with self._state_lock:
            task.status = "failed"
            task.message = "模型下载失败"
            task.error = error

    def _finish_canceled(self, task: OllamaPullTask) -> None:
        with self._state_lock:
            task.status = "canceled"
            task.message = "下载已取消"
            task.error = None


def _validate_model_name(model: str) -> str:
    """校验模型标签并返回原始规范值。"""
    if not MODEL_NAME_PATTERN.fullmatch(model):
        raise ValueError("invalid Ollama model name")
    return model


def _validate_loopback_base_url(base_url: str) -> str:
    """只接受没有凭据和额外路径的本机 HTTP Ollama 地址。"""
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
    """把合法非负整数进度值归一化，忽略布尔值和其他类型。"""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
