#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
HOST="${EVALHUB_HOST:-127.0.0.1}"
PORT="${EVALHUB_PORT:-8000}"

echo "Starting EvalHub at http://${HOST}:${PORT}"
exec "$PYTHON" run_evalhub.py serve --host "$HOST" --port "$PORT"
