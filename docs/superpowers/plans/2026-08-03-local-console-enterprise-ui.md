# Local Console Enterprise UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打磨 EvalHub 本地控制台，使其具备中文企业级 UI、Ollama 状态检测、一键启动 Ollama、默认完整数据集评测。

**Architecture:** 保持当前零依赖 Python HTTP server + 静态前端架构。新增独立 `evalhub.ollama` 模块承载状态检测，CLI 和 server 复用该模块；前端通过 API 显示状态并提交样本模式。

**Tech Stack:** Python 标准库、HTML、CSS、原生 JavaScript、unittest。

## Global Constraints

- 不引入 npm 构建链。
- 不引入后端第三方依赖。
- UI 文案以中文为主。
- `--limit` 不传时表示完整数据集。
- 一键启动脚本只启动不存在的 Ollama 服务，不重复启动。

---

### Task 1: Ollama Status Module

**Files:**
- Create: `src/evalhub/ollama.py`
- Test: `tests/test_ollama_status.py`

**Interfaces:**
- Produces: `get_ollama_status(model: str, base_url: str) -> dict[str, object]`
- Produces: `find_ollama_command() -> str | None`

- [ ] Write failing tests for installed/running/model-present states.
- [ ] Implement command discovery and `/api/tags` probing.
- [ ] Run focused unittest.

### Task 2: Backend API and CLI Full Limit

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `src/evalhub/cli.py`
- Test: `tests/test_cli_parser.py`

**Interfaces:**
- Produces: `GET /api/ollama/status?model=qwen2.5:0.5b&base_url=http://127.0.0.1:11434`
- Changes: `run-benchmark --limit` default becomes `None`.

- [ ] Write failing parser test for default `limit is None`.
- [ ] Add server status API.
- [ ] Parse missing/empty `limit` as full dataset.
- [ ] Run focused tests.

### Task 3: Startup Script

**Files:**
- Modify: `scripts/start_local.sh`

**Interfaces:**
- Uses: `curl http://127.0.0.1:11434/api/tags`
- Writes: `.runtime/ollama.log`

- [ ] Add shell functions to find Ollama command.
- [ ] Start `ollama serve` only when status endpoint is unavailable.
- [ ] Track child PID and stop only child on Ctrl+C.

### Task 4: Frontend Polish

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/app.js`

**Interfaces:**
- Consumes: `/api/ollama/status`
- Sends: `sample_mode` and optional `limit` to `/api/evaluations/run`

- [ ] Replace current layout with refined blue-white workspace.
- [ ] Add Ollama status card.
- [ ] Add sample mode segmented controls.
- [ ] Hide custom count unless custom mode is selected.
- [ ] Render result summary and JSON panel cleanly.
- [ ] Run `node --check frontend/app.js`.

### Task 5: Docs and Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/LOCAL_RUN.md`
- Modify: `docs/getting-started/OLLAMA.md`

**Verification:**
- Run: `PYTHONPATH=src .venv/bin/python -m unittest discover tests`
- Run: `.venv/bin/python -m compileall -q src run_evalhub.py tests`
- Run: `node --check frontend/app.js`
