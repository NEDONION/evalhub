from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stop_existing_evalhub.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stop_existing_evalhub", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load stop_existing_evalhub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_until_listening(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket() as client:
            if client.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"test server did not listen on {port}")


def start_health_server(port: int, service: str) -> subprocess.Popen[str]:
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
    process = subprocess.Popen(
        [sys.executable, "-c", program, service, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    wait_until_listening(port)
    return process


@unittest.skipUnless(shutil.which("lsof"), "lsof is required")
class StopExistingEvalHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_normalizes_wildcard_host_for_health_probe(self) -> None:
        self.assertEqual(self.module.normalize_probe_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(self.module.normalize_probe_host("127.0.0.1"), "127.0.0.1")

    def test_idle_port_is_a_noop(self) -> None:
        self.assertEqual(self.module.stop_existing_evalhub("127.0.0.1", free_port()), [])

    def test_stops_confirmed_evalhub_and_releases_port(self) -> None:
        port = free_port()
        process = start_health_server(port, "evalhub")
        try:
            stopped = self.module.stop_existing_evalhub("127.0.0.1", port, timeout=2)
            self.assertIn(process.pid, stopped)
            process.wait(timeout=2)
            self.assertEqual(self.module.listener_pids(port), [])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)

    def test_does_not_stop_unknown_service(self) -> None:
        port = free_port()
        process = start_health_server(port, "other-service")
        try:
            with self.assertRaisesRegex(RuntimeError, "not EvalHub"):
                self.module.stop_existing_evalhub("127.0.0.1", port, timeout=1)
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=2)
