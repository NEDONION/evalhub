"""验证本地 HTTP 服务的前端目录和资产管理 API。"""

import json
import threading
import unittest
from contextlib import contextmanager
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from evalhub.server import EvalHubRequestHandler, frontend_directory

PULLING_TASK = {
    "model": "qwen2.5:1.5b",
    "status": "pulling",
    "message": "pulling layer",
    "completed_bytes": 50,
    "total_bytes": 100,
    "speed_bytes_per_second": 25.0,
    "eta_seconds": 2,
    "error": None,
}


class FakePullManager:
    """记录 HTTP 层传入值并返回完整下载任务。"""

    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.task: dict[str, object] | None = None
        self.start_error = start_error
        self.started_with: tuple[str, str] | None = None
        self.canceled_model: str | None = None

    def start(self, model: str, base_url: str) -> dict[str, object]:
        if self.start_error is not None:
            raise self.start_error
        self.started_with = (model, base_url)
        self.task = dict(PULLING_TASK)
        return dict(self.task)

    def get(self, model: str) -> dict[str, object] | None:
        if self.task is None or self.task["model"] != model:
            return None
        return dict(self.task)

    def cancel(self, model: str) -> dict[str, object] | None:
        if self.task is None or self.task["model"] != model:
            return None
        self.canceled_model = model
        self.task = {**self.task, "status": "canceled", "message": "下载已取消"}
        return dict(self.task)


@contextmanager
def running_server(manager: FakePullManager):
    """启动只在测试期间存活的真实回环 HTTP 服务。"""
    with TemporaryDirectory() as temp_dir:
        static = Path(temp_dir)
        (static / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
        handler = partial(EvalHubRequestHandler, directory=str(static))
        with patch("evalhub.server.OLLAMA_PULL_MANAGER", manager, create=True):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server.server_port
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


def request_json(
    port: int,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    """向测试服务发送 JSON 请求并返回状态码与解码正文。"""
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode())
    finally:
        connection.close()


class FrontendDirectoryTests(unittest.TestCase):
    """保护前端构建目录发现与缺失构建时的诊断行为。"""

    def test_uses_vite_dist_directory(self) -> None:
        """存在入口文件时应返回项目根目录下的 ``frontend/dist``。"""
        # 临时目录完整模拟项目结构，避免测试依赖仓库当前是否执行过前端构建。
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = root / "frontend" / "dist"
            dist.mkdir(parents=True)
            # 入口文件是构建完成的最小判据，正文内容本身不影响目录选择。
            (dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")

            # 返回路径必须精确指向 dist，不能回退到包含 TypeScript 源码的 frontend 根目录。
            self.assertEqual(frontend_directory(root), dist)

    def test_requires_a_built_frontend(self) -> None:
        """缺少构建入口时应提示执行明确的 npm 构建命令。"""
        # 空临时项目代表首次启动但尚未构建 React 控制台的环境。
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "npm --prefix frontend run build"):
                frontend_directory(Path(temp_dir))


class OllamaPullRouteTests(unittest.TestCase):
    """保护模型下载创建、查询、取消和输入错误的 HTTP 契约。"""

    def test_creates_queries_and_cancels_model_pull(self) -> None:
        """三个方法必须共享同一模型任务并返回约定状态码。"""
        manager = FakePullManager()
        with running_server(manager) as port:
            missing_status, missing = request_json(
                port, "GET", "/api/ollama/pulls?model=qwen2.5%3A1.5b"
            )
            created_status, created = request_json(
                port,
                "POST",
                "/api/ollama/pulls",
                {
                    "model": "qwen2.5:1.5b",
                    "base_url": "http://127.0.0.1:11434",
                },
            )
            queried_status, queried = request_json(
                port, "GET", "/api/ollama/pulls?model=qwen2.5%3A1.5b"
            )
            canceled_status, canceled = request_json(
                port, "DELETE", "/api/ollama/pulls?model=qwen2.5%3A1.5b"
            )

        self.assertEqual((missing_status, missing), (200, {"ok": True, "task": None}))
        self.assertEqual(created_status, 202)
        self.assertEqual(created["task"]["status"], "pulling")
        self.assertEqual(
            manager.started_with,
            ("qwen2.5:1.5b", "http://127.0.0.1:11434"),
        )
        self.assertEqual(queried_status, 200)
        self.assertEqual(queried["task"]["completed_bytes"], 50)
        self.assertEqual(canceled_status, 200)
        self.assertEqual(canceled["task"]["status"], "canceled")
        self.assertEqual(manager.canceled_model, "qwen2.5:1.5b")

    def test_converts_pull_validation_error_to_http_400(self) -> None:
        """管理器拒绝的远端地址必须成为可诊断客户端错误。"""
        manager = FakePullManager(start_error=ValueError("loopback only"))
        with running_server(manager) as port:
            status, body = request_json(
                port,
                "POST",
                "/api/ollama/pulls",
                {"model": "qwen2.5:1.5b", "base_url": "http://example.com:11434"},
            )

        self.assertEqual(status, 400)
        self.assertEqual(body, {"ok": False, "error": "loopback only"})

    def test_cancel_unknown_pull_returns_http_404(self) -> None:
        """取消不存在的任务必须与成功取消明确区分。"""
        with running_server(FakePullManager()) as port:
            status, body = request_json(
                port, "DELETE", "/api/ollama/pulls?model=missing%3A1b"
            )

        self.assertEqual(status, 404)
        self.assertEqual(body, {"ok": False, "error": "pull task not found"})


if __name__ == "__main__":
    # 支持直接运行该文件，快速验证本地静态资源目录约束。
    unittest.main()
