"""验证端口释放工具只停止已确认的 EvalHub，并保护未知本地服务。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType

# 测试通过仓库相对路径动态加载脚本，避免要求 ``scripts`` 成为可导入 Python 包。
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stop_existing_evalhub.py"


def load_module() -> ModuleType:
    """从真实脚本路径加载端口释放模块供测试调用。

    Returns:
        已执行并暴露公开辅助函数的动态模块对象。

    Raises:
        RuntimeError: Python 无法为目标脚本创建有效的模块加载器。
    """
    # 使用文件位置构造模块规格，保证测试覆盖启动脚本实际调用的同一份实现。
    spec = importlib.util.spec_from_file_location("stop_existing_evalhub", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load stop_existing_evalhub.py")
    # 先按规格创建模块，再由加载器执行源码以填充待测函数。
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    """向操作系统申请并返回当前可绑定的临时本地端口。

    Returns:
        操作系统为回环地址分配的空闲 TCP 端口号。
    """
    # 绑定端口 0 让内核选择可用端口，退出上下文后立即释放给测试子进程。
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_until_listening(port: int) -> None:
    """等待测试健康服务开始监听指定端口。

    Args:
        port: 测试子进程应当监听的回环地址端口。

    Raises:
        RuntimeError: 五秒内始终无法连接测试服务。
    """
    # 单调时钟不受系统时间调整影响，使真实子进程的启动等待稳定可重复。
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket() as client:
            if client.connect_ex(("127.0.0.1", port)) == 0:
                return
        # 短间隔轮询兼顾测试速度和避免忙等占满 CPU。
        time.sleep(0.05)
    raise RuntimeError(f"test server did not listen on {port}")


def start_health_server(port: int, service: str) -> subprocess.Popen[str]:
    """启动返回指定服务标识的隔离 HTTP 健康检查子进程。

    Args:
        port: 子进程需要监听的回环地址端口。
        service: 健康响应中用于身份验证的服务名称。

    Returns:
        已确认开始监听的 Python 子进程句柄。
    """
    # 内联标准库服务器让测试无需依赖 EvalHub 主进程，同时保留真实 HTTP 与信号行为。
    program = r'''\
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, sys
service, port = sys.argv[1], int(sys.argv[2])
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "service": service}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_args):
        pass
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''
    # 丢弃子进程输出，避免测试日志污染；参数分别传递服务名与动态端口。
    process = subprocess.Popen(
        [sys.executable, "-c", program, service, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    # 返回前确认端口已经监听，消除健康探测与子进程启动之间的竞态条件。
    wait_until_listening(port)
    return process


@unittest.skipUnless(shutil.which("lsof"), "lsof is required")
class StopExistingEvalHubTests(unittest.TestCase):
    """覆盖端口空闲、EvalHub 身份确认和未知服务保护等真实进程场景。"""

    @classmethod
    def setUpClass(cls) -> None:
        """在整组测试开始前加载一次真实端口释放脚本模块。"""
        # 所有测试共享无可变全局状态的模块对象，减少重复动态导入开销。
        cls.module = load_module()

    def test_normalizes_wildcard_host_for_health_probe(self) -> None:
        """通配监听地址应转换为回环地址，具体地址则保持不变。"""
        # 两个断言同时保护特殊映射和普通主机地址的透明传递行为。
        self.assertEqual(self.module.normalize_probe_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(self.module.normalize_probe_host("127.0.0.1"), "127.0.0.1")

    def test_idle_port_is_a_noop(self) -> None:
        """目标端口没有监听者时应直接成功且不返回任何进程编号。"""
        # 动态申请的空闲端口保证测试不会依赖开发机上的固定端口状态。
        self.assertEqual(self.module.stop_existing_evalhub("127.0.0.1", free_port()), [])

    def test_stops_confirmed_evalhub_and_releases_port(self) -> None:
        """健康接口确认服务身份后应优雅终止进程并释放监听端口。"""
        # 启动服务标识为 EvalHub 的真实子进程，覆盖健康探测、信号与端口轮询全链路。
        port = free_port()
        process = start_health_server(port, "evalhub")
        try:
            # 较短超时让失败快速暴露，同时为本地子进程处理 ``SIGTERM`` 留出充足时间。
            stopped = self.module.stop_existing_evalhub("127.0.0.1", port, timeout=2)
            self.assertIn(process.pid, stopped)
            process.wait(timeout=2)
            # 同时断言子进程退出和 ``lsof`` 无监听者，避免只终止进程却未验证端口释放。
            self.assertEqual(self.module.listener_pids(port), [])
        finally:
            # 断言提前失败时也回收测试子进程，防止残留服务影响后续用例或本地开发。
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)

    def test_does_not_stop_unknown_service(self) -> None:
        """健康标识不是 EvalHub 时应拒绝终止并保持原服务继续运行。"""
        # 使用同样的真实 HTTP 子进程但替换服务标识，精确验证误杀保护分支。
        port = free_port()
        process = start_health_server(port, "other-service")
        try:
            # 未知服务必须产生可诊断错误，且其进程在拒绝操作后仍然存活。
            with self.assertRaisesRegex(RuntimeError, "not EvalHub"):
                self.module.stop_existing_evalhub("127.0.0.1", port, timeout=1)
            self.assertIsNone(process.poll())
        finally:
            # 该场景按设计不会由待测工具停止，因此测试自身始终负责清理子进程。
            process.terminate()
            process.wait(timeout=2)


class LauncherIntegrationTests(unittest.TestCase):
    """验证本地启动脚本先安全释放端口，再启动新的 EvalHub 进程。"""

    def test_launcher_stops_existing_evalhub_before_starting(self) -> None:
        """启动器应以相同主机和端口先调用停止工具，再调用服务入口。"""
        # 临时目录隔离伪命令和调用日志，测试结束后由上下文自动清理全部产物。
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            call_log = temporary / "python-calls.log"

            # 伪造成功的 npm 与 curl，跳过真实前端构建和 Ollama 网络探测副作用。
            fake_npm = fake_bin / "npm"
            fake_npm.write_text("#!/bin/sh\nexit 0\n")
            fake_npm.chmod(0o755)

            # curl 替身额外记录健康探测，确保断言可以验证停止旧服务发生在 Ollama 检查之前。
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$EVALHUB_CALL_LOG"\nexit 0\n'
            )
            fake_curl.chmod(0o755)

            # 伪 Python 只记录参数而不运行服务，使测试能够观察两个命令的真实调用顺序。
            fake_python = fake_bin / "python"
            fake_python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$EVALHUB_CALL_LOG"\n'
            )
            fake_python.chmod(0o755)

            # 继承正常环境并只覆盖测试所需的 PATH、解释器和日志位置。
            environment = os.environ.copy()
            environment.update(
                {
                    "EVALHUB_CALL_LOG": str(call_log),
                    "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                    "PYTHON": str(fake_python),
                }
            )

            # 从仓库根目录执行真实启动脚本，捕获输出并禁止非零状态自动抛出异常。
            result = subprocess.run(
                [str(ROOT / "scripts" / "start_local.sh")],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            # 启动流程必须成功，且停止命令严格位于服务启动命令之前并共享连接参数。
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                call_log.read_text().splitlines(),
                [
                    '-c import immutabledict, langdetect, lm_eval, nltk, transformers',
                    'scripts/stop_existing_evalhub.py --host 127.0.0.1 --port 8000',
                    'curl -fsS http://127.0.0.1:11434/api/tags',
                    'run_evalhub.py serve --host 127.0.0.1 --port 8000',
                ],
            )
