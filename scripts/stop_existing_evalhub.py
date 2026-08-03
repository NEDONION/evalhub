#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from urllib.request import urlopen


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
        message = result.stderr.strip() or f"lsof failed with exit code {result.returncode}"
        raise RuntimeError(message)

    try:
        return sorted({int(line) for line in result.stdout.splitlines() if line.strip()})
    except ValueError as exc:
        raise RuntimeError(f"lsof returned an invalid PID for port {port}") from exc


def is_evalhub(host: str, port: int) -> bool:
    probe_host = normalize_probe_host(host)
    url = f"http://{probe_host}:{port}/api/health"
    try:
        with urlopen(url, timeout=1.0) as response:
            payload = json.load(response)
            return (
                response.status == 200
                and isinstance(payload, dict)
                and payload.get("service") == "evalhub"
            )
    except (OSError, ValueError):
        return False


def stop_existing_evalhub(host: str, port: int, timeout: float = 5.0) -> list[int]:
    pids = listener_pids(port)
    if not pids:
        return []

    if not is_evalhub(host, port):
        joined = ", ".join(str(pid) for pid in pids)
        raise RuntimeError(f"port {port} is occupied by PID {joined}, but it is not EvalHub")

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise RuntimeError(f"permission denied while stopping EvalHub PID {pid}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not listener_pids(port):
            return pids
        time.sleep(0.1)

    joined = ", ".join(str(pid) for pid in pids)
    raise RuntimeError(
        f"EvalHub PID {joined} did not release port {port} within {timeout:g}s"
    )


def positive_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def positive_timeout(value: str) -> float:
    timeout = float(value)
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely stop an existing EvalHub that owns a local port."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=positive_port, default=8000)
    parser.add_argument("--timeout", type=positive_timeout, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        stopped = stop_existing_evalhub(args.host, args.port, args.timeout)
    except RuntimeError as exc:
        print(f"Cannot start EvalHub: {exc}", file=sys.stderr)
        return 1

    if stopped:
        joined = ", ".join(str(pid) for pid in stopped)
        print(f"Stopped previous EvalHub PID {joined} on port {args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
