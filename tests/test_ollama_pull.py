"""验证 Ollama 模型下载任务的输入边界、进度和生命周期。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from typing import Any
from urllib.request import Request

import pytest

from evalhub.ollama_pull import OllamaPullManager


class FakeResponse:
    """用完整 Ollama NDJSON 事件序列替代真实网络响应。"""

    def __init__(self, events: Iterable[dict[str, object]]) -> None:
        self.events = list(events)
        self.closed = False

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self):
        for event in self.events:
            if self.closed:
                return
            yield (json.dumps(event) + "\n").encode()

    def close(self) -> None:
        self.closed = True


class BlockingResponse(FakeResponse):
    """在第一条进度后阻塞，允许测试取消和全局串行。"""

    def __init__(self) -> None:
        super().__init__([])
        self.started = threading.Event()
        self.release = threading.Event()

    def __iter__(self):
        self.started.set()
        yield b'{"status":"pulling layer","completed":10,"total":100}\n'
        self.release.wait(timeout=2)
        if not self.closed:
            yield b'{"status":"success"}\n'

    def close(self) -> None:
        self.closed = True
        self.release.set()


def wait_for_status(
    manager: OllamaPullManager,
    model: str,
    expected: set[str],
    *,
    timeout: float = 2,
) -> dict[str, object]:
    """等待后台任务进入期望状态，超时则暴露最后状态。"""
    deadline = time.monotonic() + timeout
    task = manager.get(model)
    while task is not None and task["status"] not in expected and time.monotonic() < deadline:
        time.sleep(0.01)
        task = manager.get(model)
    assert task is not None
    assert task["status"] in expected
    return task


def test_rejects_non_loopback_ollama_url() -> None:
    """模型下载不得把服务端变成任意远端请求代理。"""
    manager = OllamaPullManager(opener=lambda *_args, **_kwargs: FakeResponse([]))

    with pytest.raises(ValueError, match="loopback"):
        manager.start("qwen2.5:1.5b", "http://example.com:11434")


@pytest.mark.parametrize(
    "model",
    ["", "../model", "qwen 2.5:1.5b", "qwen2.5:bad/tag"],
)
def test_rejects_invalid_model_names(model: str) -> None:
    """只允许 Ollama 标签使用的安全模型名字符集合。"""
    manager = OllamaPullManager(opener=lambda *_args, **_kwargs: FakeResponse([]))

    with pytest.raises(ValueError, match="model"):
        manager.start(model, "http://127.0.0.1:11434")


def test_tracks_pull_progress_and_sends_streaming_request() -> None:
    """真实 Pull 事件必须转换为完成字节、速度、ETA 和成功状态。"""
    captured: list[tuple[Request, int]] = []
    response = FakeResponse(
        [
            {"status": "pulling manifest"},
            {"status": "pulling layer", "completed": 25, "total": 100},
            {"status": "pulling layer", "completed": 50, "total": 100},
            {"status": "verifying sha256 digest"},
            {"status": "success"},
        ]
    )
    clock_values = iter([1.0, 2.0])

    def opener(request: Request, timeout: int) -> FakeResponse:
        captured.append((request, timeout))
        return response

    manager = OllamaPullManager(opener=opener, clock=lambda: next(clock_values))
    first = manager.start("qwen2.5:1.5b", "http://127.0.0.1:11434/")
    task = wait_for_status(manager, "qwen2.5:1.5b", {"success"})

    assert first["model"] == "qwen2.5:1.5b"
    assert len(captured) == 1
    request, timeout = captured[0]
    assert request.full_url == "http://127.0.0.1:11434/api/pull"
    assert json.loads(request.data or b"{}") == {"model": "qwen2.5:1.5b", "stream": True}
    assert timeout == 30
    assert task["completed_bytes"] == 50
    assert task["total_bytes"] == 100
    assert task["speed_bytes_per_second"] == 25.0
    assert task["eta_seconds"] == 2
    assert task["error"] is None


def test_maps_ollama_error_event_to_failed_task() -> None:
    """Ollama 流中的错误必须成为可查询任务失败而不是线程静默退出。"""
    manager = OllamaPullManager(
        opener=lambda *_args, **_kwargs: FakeResponse([{"error": "model not found"}])
    )

    manager.start("missing:1b", "http://localhost:11434")
    task = wait_for_status(manager, "missing:1b", {"failed"})

    assert task["error"] == "model not found"
    assert task["message"] == "模型下载失败"


def test_cancel_closes_active_response_and_preserves_canceled_state() -> None:
    """取消必须关闭活动响应且不能被后台线程覆盖为成功。"""
    response = BlockingResponse()
    manager = OllamaPullManager(opener=lambda *_args, **_kwargs: response)
    first = manager.start("qwen2.5:1.5b", "http://127.0.0.1:11434")
    assert response.started.wait(timeout=1)
    duplicate = manager.start("qwen2.5:1.5b", "http://127.0.0.1:11434")

    task = manager.cancel("qwen2.5:1.5b")
    terminal = wait_for_status(manager, "qwen2.5:1.5b", {"canceled"})

    assert task is not None
    assert duplicate["model"] == first["model"]
    assert response.closed is True
    assert terminal["status"] == "canceled"


def test_serializes_different_model_downloads() -> None:
    """第二个模型必须等待第一个任务结束后才占用下载带宽。"""
    first_response = BlockingResponse()
    second_response = FakeResponse([{"status": "success"}])
    responses: dict[str, Any] = {
        "first:1b": first_response,
        "second:1b": second_response,
    }

    def opener(request: Request, timeout: int) -> FakeResponse:
        assert timeout == 30
        payload = json.loads(request.data or b"{}")
        return responses[payload["model"]]

    manager = OllamaPullManager(opener=opener)
    manager.start("first:1b", "http://127.0.0.1:11434")
    assert first_response.started.wait(timeout=1)
    manager.start("second:1b", "http://127.0.0.1:11434")
    time.sleep(0.05)

    waiting = manager.get("second:1b")
    assert waiting is not None
    assert waiting["status"] == "pending"

    manager.cancel("first:1b")
    wait_for_status(manager, "first:1b", {"canceled"})
    wait_for_status(manager, "second:1b", {"success"})
