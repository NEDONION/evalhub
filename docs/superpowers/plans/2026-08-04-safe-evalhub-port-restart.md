# Safe EvalHub Port Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/start_local.sh` safely stop a previously running EvalHub on the configured port before starting the new instance.

**Architecture:** A focused Python helper owns listener discovery, EvalHub health verification, graceful termination, and release waiting. The shell launcher remains an orchestrator and invokes the helper immediately before starting the new server.

**Tech Stack:** Python 3.11+ standard library, `unittest`, Bash, macOS/Linux `lsof`.

## Global Constraints

- Terminate a listener only when `GET /api/health` returns JSON with `service` equal to `evalhub`.
- Never terminate an unknown service and never escalate automatically to `SIGKILL`.
- Wait at most 5 seconds for the configured port to be released.
- Use `127.0.0.1` as the probe host when the listen host is `0.0.0.0`.
- Preserve all unrelated working-tree changes.

---

## File Structure

- Create `scripts/stop_existing_evalhub.py`: safe port-owner detection and graceful shutdown command.
- Create `tests/test_stop_existing_evalhub.py`: real-process behavior tests plus launcher integration assertion.
- Modify `scripts/start_local.sh`: invoke the helper before `run_evalhub.py serve`.

### Task 1: Safe Existing-Server Stopper

**Files:**
- Create: `scripts/stop_existing_evalhub.py`
- Test: `tests/test_stop_existing_evalhub.py`

**Interfaces:**
- Produces: `normalize_probe_host(host: str) -> str`
- Produces: `listener_pids(port: int) -> list[int]`
- Produces: `stop_existing_evalhub(host: str, port: int, timeout: float = 5.0) -> list[int]`
- Produces CLI: `python scripts/stop_existing_evalhub.py --host HOST --port PORT [--timeout SECONDS]`

- [ ] **Step 1: Write failing behavior tests**

Create `tests/test_stop_existing_evalhub.py` with:

```python
from __future__ import annotations

import importlib.util
import json
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
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_stop_existing_evalhub -v
```

Expected: error because `scripts/stop_existing_evalhub.py` does not exist.

- [ ] **Step 3: Implement the minimal safe stopper**

Create `scripts/stop_existing_evalhub.py` with `argparse`, `json`, `os`, `signal`, `subprocess`, `time`, and `urllib.request` from the standard library. Implement:

```python
def normalize_probe_host(host: str) -> str:
    return "127.0.0.1" if host == "0.0.0.0" else host


def listener_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("lsof is required to inspect the EvalHub port") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or f"lsof failed with exit code {result.returncode}")
    return sorted({int(line) for line in result.stdout.splitlines() if line.strip()})


def is_evalhub(host: str, port: int) -> bool:
    probe_host = normalize_probe_host(host)
    url = f"http://{probe_host}:{port}/api/health"
    try:
        with urlopen(url, timeout=1.0) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return False
    return response.status == 200 and payload.get("service") == "evalhub"


def stop_existing_evalhub(host: str, port: int, timeout: float = 5.0) -> list[int]:
    pids = listener_pids(port)
    if not pids:
        return []
    if not is_evalhub(host, port):
        joined = ", ".join(str(pid) for pid in pids)
        raise RuntimeError(f"port {port} is occupied by PID {joined}, but it is not EvalHub")
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not listener_pids(port):
            return pids
        time.sleep(0.1)
    joined = ", ".join(str(pid) for pid in pids)
    raise RuntimeError(f"EvalHub PID {joined} did not release port {port} within {timeout:g}s")
```

Add a CLI `main()` that validates `1 <= port <= 65535` and `timeout > 0`, prints `Stopping previous EvalHub on port ...` when PIDs exist, prints failures to stderr, and exits with status `1` for `RuntimeError`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_stop_existing_evalhub -v
```

Expected: 4 tests pass and the real child EvalHub process is terminated gracefully.

- [ ] **Step 5: Commit the safe stopper**

```bash
git add scripts/stop_existing_evalhub.py tests/test_stop_existing_evalhub.py
git commit -m "feat: safely stop an existing EvalHub"
```

### Task 2: Launcher Integration

**Files:**
- Modify: `scripts/start_local.sh`
- Modify: `tests/test_stop_existing_evalhub.py`

**Interfaces:**
- Consumes: `scripts/stop_existing_evalhub.py --host HOST --port PORT`
- Produces: startup ordering where safe cleanup completes before `run_evalhub.py serve`

- [ ] **Step 1: Write the failing launcher behavior test**

Add a test that runs the real `scripts/start_local.sh` with a temporary `PATH`. Provide a fake `npm` that exits `0`, a fake `curl` that exits `0` so the script treats Ollama as already running, and a fake `PYTHON` executable that appends every argument list to a temporary log. Assert the launcher exits `0` and the log contains these two literal lines in order:

```text
scripts/stop_existing_evalhub.py --host 127.0.0.1 --port 8000
run_evalhub.py serve --host 127.0.0.1 --port 8000
```

This test exercises the launcher's observable command ordering instead of inspecting its source text.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_stop_existing_evalhub.LauncherIntegrationTests.test_launcher_stops_existing_evalhub_before_starting -v
```

Expected: FAIL because the launcher does not yet call the stopper.

- [ ] **Step 3: Wire the stopper into the launcher**

Immediately before the existing `Starting EvalHub` message, add:

```bash
"$PYTHON" scripts/stop_existing_evalhub.py --host "$HOST" --port "$PORT"
```

Do not alter the existing Ollama log fallback changes already present in `scripts/start_local.sh`.

- [ ] **Step 4: Run focused and syntax tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_stop_existing_evalhub -v
bash -n scripts/start_local.sh
```

Expected: all 5 focused tests pass and Bash syntax exits `0`.

- [ ] **Step 5: Commit only the launcher hunk and test**

Because `scripts/start_local.sh` contains unrelated working-tree changes, stage only the new stopper invocation hunk plus the test change. Confirm the cached diff does not include the existing `OLLAMA_LOG` fallback changes.

```bash
git add tests/test_stop_existing_evalhub.py
git diff -- scripts/start_local.sh
git add -p scripts/start_local.sh
git diff --cached
git commit -m "fix: restart the local EvalHub safely"
```

### Task 3: Verification

**Files:**
- Verify only; no planned production changes.

**Interfaces:**
- Consumes: completed safe stopper and launcher integration.
- Produces: fresh completion evidence.

- [ ] **Step 1: Run the full Python and launcher checks**

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover tests
.venv/bin/python -m compileall -q src scripts run_evalhub.py tests
bash -n scripts/start_local.sh
```

Expected: all tests pass and both static checks exit `0`.

- [ ] **Step 2: Run a real port-conflict regression**

Start EvalHub on a free temporary port, then invoke `scripts/stop_existing_evalhub.py` against it. Verify the helper reports the old PID, the process exits, and `lsof` shows no listener. Repeat with a non-EvalHub HTTP server and verify the helper exits `1` while the server remains running.

- [ ] **Step 3: Inspect repository state**

```bash
git diff --check
git status --short
git log --oneline --max-count=5
```

Expected: no whitespace errors, implementation committed, and only pre-existing unrelated changes remain unstaged.
