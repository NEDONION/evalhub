# EvalHub Full Benchmark Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接通 `llm-industry-core-v1` 的全部 13 个真实 Benchmark，并在 React 控制台展示、缓存和提交它们。

**Architecture:** 保留 GSM8K/MMLU 原生执行；新增一个薄 `lm-eval` 适配模块负责官方任务加载、结果转换和代码任务 Docker 命令。现有 SQLite DAG 只按 Registry 分派执行器，API 与前端都从同一 Registry 读取 13 项。

**Tech Stack:** Python 3.11+、lm-eval 0.4.12、Ollama OpenAI completions、Docker、SQLite、React 19、TypeScript、Vitest。

## Global Constraints

- 默认样本模式保持 `all`，只有用户明确选择 quick/custom 才传 limit。
- HumanEval/MBPP 只能在受限 Docker 容器执行生成代码。
- 不新增任务队列、ORM、状态库或第二套 Benchmark Registry。
- 单元测试不得访问真实网络、Ollama 或 Docker。

---

### Task 1: 官方 Harness 边界

**Files:**
- Create: `src/evalhub/benchmarks/harness.py`
- Create: `tests/test_benchmark_harness.py`
- Create: `docker/lm-eval.Dockerfile`
- Modify: `src/evalhub/benchmarks/registry.py`
- Modify: `src/evalhub/benchmarks/__init__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `prepare_harness_benchmark(benchmark_id: str) -> HarnessAsset`
- Produces: `run_harness_benchmark(..., on_sample_result: Callable) -> dict[str, object]`
- Produces: `benchmark_readiness(spec: BenchmarkSpec, model: str | None = None) -> tuple[bool, str | None]`

- [ ] **Step 1: Write failing tests** for 13 registry entries, `lm-eval==0.4.12` task names, tokenizer mapping, result/sample conversion, and Docker security arguments.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_benchmark_harness.py tests/test_benchmark_registry.py -q` and confirm missing module/task-name failures.
- [ ] **Step 3: Implement minimal adapter** using lazy `lm_eval` imports, `simple_evaluate(model="local-completions", ...)`, a fixed tokenizer mapping, and `subprocess.run([...], check=True)` for Docker code tasks.
- [ ] **Step 4: Run the same tests** and confirm PASS.
- [ ] **Step 5: Commit** `feat: add official benchmark harness`.

### Task 2: SQLite Workflow 分派

**Files:**
- Modify: `src/evalhub/tasks/runtime.py`
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_task_executor.py`

**Interfaces:**
- Consumes: Task 1 Harness functions.
- Produces: all three `ExecutorKind` values supported by `prepare_assets` and benchmark nodes.

- [ ] **Step 1: Write failing tests** proving non-native assets become ready, executor kind reaches the child process, Harness samples persist in SQLite, and failed external nodes remain retryable.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_workflow_runtime.py tests/test_task_executor.py -q` and confirm `executor_not_ready` failures.
- [ ] **Step 3: Implement minimal dispatch** in the existing child-process and runtime paths; retain native digest/checkpoint behavior and use node-level retry for Harness failures.
- [ ] **Step 4: Run the same tests** and confirm PASS.
- [ ] **Step 5: Commit** `feat: run all registry benchmark executors`.

### Task 3: 13 项资产 API

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_server_frontend.py`
- Modify: `tests/test_task_api.py`
- Modify: `scripts/start_local.sh`
- Modify: `README.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`

**Interfaces:**
- Produces: `GET /api/datasets` with exactly 13 Registry-backed items.
- Produces: `POST /api/datasets/prepare` for native, lm-eval, and Docker-backed items.
- Produces: readiness counts used by `/api/benchmarks` and `/api/suites`.

- [ ] **Step 1: Write failing API tests** asserting 13 assets, all executor labels, full suite readiness under patched dependencies, and prepare dispatch for an external task.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_server_frontend.py tests/test_task_api.py -q` and confirm the API still returns only two assets.
- [ ] **Step 3: Implement Registry-backed responses** while preserving native paths/counts; add Harness cache markers/status and one-click dependency/image setup diagnostics.
- [ ] **Step 4: Run the same tests** and confirm PASS.
- [ ] **Step 5: Commit** `feat: expose all benchmark assets`.

### Task 4: React 资产页与表单

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/dashboard/DatasetTable.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 3 API fields `executor`, `capability_label`, `readiness_reason`, `prepared`, and `sample_count`.
- Produces: 13 visible asset rows and selectable locally runnable Benchmark options.

- [ ] **Step 1: Write a failing component test** that supplies all 13 assets and verifies the count, external runtime label, and enabled MMLU-Pro option.
- [ ] **Step 2: Run** `npm --prefix frontend run test:run -- App.test.tsx` and confirm the old two-dataset assumptions fail.
- [ ] **Step 3: Update types and rendering** with Chinese executor/cache status; remove obsolete “only 2 connected” copy while retaining responsive table behavior.
- [ ] **Step 4: Run** frontend test, typecheck, and build; confirm PASS.
- [ ] **Step 5: Commit** `feat: show all benchmarks in console`.

### Task 5: End-to-End Verification

**Files:**
- Modify only files required by failures found below.

**Interfaces:**
- Consumes: Tasks 1-4 completed behavior.
- Produces: verified local API and rendered UI evidence.

- [ ] **Step 1: Run** `.venv/bin/python -m ruff check .` and `.venv/bin/python -m pytest`.
- [ ] **Step 2: Run** `npm --prefix frontend run test:run`, `npm --prefix frontend run typecheck`, and `npm --prefix frontend run build`.
- [ ] **Step 3: Run** `git diff --check` and inspect `git status --short` for generated artifacts.
- [ ] **Step 4: Start** `./scripts/start_local.sh`, query `/api/datasets`, `/api/benchmarks`, and `/api/suites`, and assert all report 13 connected items.
- [ ] **Step 5: Open the local UI** and verify the asset table has 13 rows and the Benchmark selector exposes all entries without overlap.
