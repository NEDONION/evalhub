#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
HOST="${EVALHUB_HOST:-127.0.0.1}"
PORT="${EVALHUB_PORT:-8000}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
OLLAMA_PID=""
OLLAMA_LOG=".runtime/ollama.log"
LM_EVAL_IMAGE="evalhub-lm-eval:0.4.12"

if [ ! -x "${PYTHON}" ]; then
  echo "Python environment not found. Run: python3 -m venv .venv"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to build the React frontend. Install Node.js 20+ first."
  exit 1
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "Frontend dependencies are not installed. Run: npm --prefix frontend install"
  exit 1
fi

if ! "${PYTHON}" -c "import lm_eval" >/dev/null 2>&1; then
  echo "Installing official Benchmark runtime..."
  "${PYTHON}" -m pip install -e ".[benchmarks]"
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if ! docker image inspect "${LM_EVAL_IMAGE}" >/dev/null 2>&1; then
    echo "Building isolated HumanEval/MBPP runtime..."
    if ! docker build -t "${LM_EVAL_IMAGE}" -f docker/lm-eval.Dockerfile .; then
      echo "Docker image build failed; HumanEval and MBPP will remain unavailable."
    fi
  fi
else
  echo "Docker Desktop is not running; HumanEval and MBPP will remain unavailable."
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

"$PYTHON" scripts/stop_existing_evalhub.py --host "$HOST" --port "$PORT"

if ollama_running; then
  echo "Ollama is already running at ${OLLAMA_BASE_URL}"
else
  if OLLAMA_BIN="$(find_ollama)"; then
    mkdir -p .runtime
    if ! : >"${OLLAMA_LOG}" 2>/dev/null; then
      OLLAMA_LOG="/tmp/evalhub-ollama.log"
      : >"${OLLAMA_LOG}"
    fi
    echo "Starting Ollama at ${OLLAMA_BASE_URL}"
    "${OLLAMA_BIN}" serve >"${OLLAMA_LOG}" 2>&1 &
    OLLAMA_PID="$!"
    sleep 2
    if ollama_running; then
      echo "Ollama started. Logs: ${OLLAMA_LOG}"
    else
      echo "Ollama did not become ready yet. Logs: ${OLLAMA_LOG}"
    fi
  else
    echo "Ollama is not installed. See docs/getting-started/20260804_Ollama本地模型安装与验证.md"
  fi
fi

echo "Starting EvalHub at http://${HOST}:${PORT}"
"$PYTHON" run_evalhub.py serve --host "$HOST" --port "$PORT"
