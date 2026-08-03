#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
HOST="${EVALHUB_HOST:-127.0.0.1}"
PORT="${EVALHUB_PORT:-8000}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
OLLAMA_PID=""

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to build the React frontend. Install Node.js 20+ first."
  exit 1
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "Frontend dependencies are not installed. Run: npm --prefix frontend install"
  exit 1
fi

echo "Building React frontend..."
npm --prefix frontend run build

find_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
    return 0
  fi

  if [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ]; then
    printf '%s\n' "/Applications/Ollama.app/Contents/Resources/ollama"
    return 0
  fi

  return 1
}

ollama_running() {
  curl -fsS "${OLLAMA_BASE_URL}/api/tags" >/dev/null 2>&1
}

cleanup() {
  if [ -n "${OLLAMA_PID}" ] && kill -0 "${OLLAMA_PID}" >/dev/null 2>&1; then
    echo "Stopping Ollama started by this script..."
    kill "${OLLAMA_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

if ollama_running; then
  echo "Ollama is already running at ${OLLAMA_BASE_URL}"
else
  if OLLAMA_BIN="$(find_ollama)"; then
    mkdir -p .runtime
    echo "Starting Ollama at ${OLLAMA_BASE_URL}"
    "${OLLAMA_BIN}" serve >.runtime/ollama.log 2>&1 &
    OLLAMA_PID="$!"
    sleep 2
    if ollama_running; then
      echo "Ollama started. Logs: .runtime/ollama.log"
    else
      echo "Ollama did not become ready yet. Logs: .runtime/ollama.log"
    fi
  else
    echo "Ollama is not installed. See docs/OLLAMA.md"
  fi
fi

echo "Starting EvalHub at http://${HOST}:${PORT}"
"$PYTHON" run_evalhub.py serve --host "$HOST" --port "$PORT"
