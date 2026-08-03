# Evaluation Task Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent, truthful evaluation task center with FIFO execution, live progress, elapsed time, CPU/memory/GPU telemetry, and result-on-detail disclosure.

**Architecture:** The existing synchronous runner remains the evaluation core. A SQLite repository and one background scheduler persist and execute tasks, while a spawned process isolates each evaluation for resource sampling and cancellation. React creates tasks and polls compact summaries; selecting a task fetches its full result.

**Tech Stack:** Python 3.11, standard-library SQLite/multiprocessing/threading, psutil, pytest, React 19, TypeScript, Tailwind CSS, Vitest, Testing Library.

## Global Constraints

- Preserve the synchronous CLI and `/api/evaluations/run` contract.
- Store runtime data only below `.runtime/`; never commit the SQLite database.
- Run exactly one evaluation task at a time and recover pending tasks in FIFO order.
- CPU and memory describe the isolated evaluation process and direct children; GPU is optional and must report unsupported rather than fabricated values.
- New or modified Python functions and methods require detailed Chinese docstrings and Chinese comments at least every five effective code lines.
- Unit tests must not access the network, download datasets, or start Ollama.
- Do not modify or stage unrelated user changes.

---

### Task 1: Runner progress contract

**Files:**
- Modify: `src/evalhub/engine/runner.py`
- Modify: `src/evalhub/cli.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: existing `EvaluationRunner.run(...)` and `run_real_benchmark(...)` calls.
- Produces: `ProgressCallback = Callable[[int, int], None]`; optional `on_progress` parameters; optional `job_id` for a pre-created task.

- [ ] **Step 1: Write the failing Runner progress test**

```python
def test_runner_reports_progress_after_each_scored_sample() -> None:
    updates: list[tuple[int, int]] = []
    results, _ = runner.run(
        job=job,
        benchmark=benchmark,
        samples=samples,
        on_progress=lambda completed, total: updates.append((completed, total)),
    )
    assert len(results) == 2
    assert updates == [(1, 2), (2, 2)]
```

- [ ] **Step 2: Run the focused test and confirm the missing keyword failure**

Run: `.venv/bin/python -m pytest tests/test_runner.py -q`

Expected: FAIL because `EvaluationRunner.run()` does not accept `on_progress`.

- [ ] **Step 3: Implement optional progress reporting**

```python
ProgressCallback = Callable[[int, int], None]

def run(..., on_progress: ProgressCallback | None = None) -> ...:
    ...
    results.append(sample_result)
    if on_progress is not None:
        on_progress(len(results), len(samples))
```

Extend `run_real_benchmark` with `job_id: str | None = None` and `on_progress: ProgressCallback | None = None`; report `(0, len(samples))` after loading, construct `EvaluationJob(id=job_id, ...)` when provided, and pass the callback into Runner.

- [ ] **Step 4: Run Runner and CLI compatibility tests**

Run: `.venv/bin/python -m pytest tests/test_runner.py tests/test_cli_parser.py -q`

Expected: PASS.

### Task 2: Persistent task model and SQLite repository

**Files:**
- Create: `src/evalhub/tasks/__init__.py`
- Create: `src/evalhub/tasks/models.py`
- Create: `src/evalhub/tasks/repository.py`
- Create: `tests/test_task_repository.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `TaskRequest`, `ResourceUsage`, `EvaluationTask`, and `SQLiteTaskRepository`.
- Produces repository methods `create`, `list`, `get`, `mark_running`, `update_progress`, `update_resources`, `mark_success`, `mark_failed`, `mark_canceled`, `recover_interrupted`, and `list_pending`.

- [ ] **Step 1: Write repository tests against a temporary database**

```python
def test_repository_persists_task_progress_resources_and_result(tmp_path: Path) -> None:
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task = repository.create(TaskRequest(...))
    repository.mark_running(task.id)
    repository.update_progress(task.id, completed=2, total=5)
    repository.update_resources(
        task.id,
        ResourceUsage(cpu_percent=42.5, memory_bytes=1024, gpu_supported=False),
    )
    repository.mark_success(task.id, evaluation_result)
    restored = SQLiteTaskRepository(tmp_path / "tasks.db").get(task.id)
    assert restored.status == "success"
    assert restored.completed_samples == 5
    assert restored.peak_cpu_percent == 42.5
    assert restored.result == evaluation_result
```

Add separate tests for descending list order, missing IDs, terminal-state guards, restart recovery, and pending FIFO order.

- [ ] **Step 2: Run the repository tests and confirm import failure**

Run: `.venv/bin/python -m pytest tests/test_task_repository.py -q`

Expected: FAIL because `evalhub.tasks` does not exist.

- [ ] **Step 3: Implement typed task records and SQLite schema**

Use timezone-aware ISO strings, JSON request/result columns, WAL, `busy_timeout=5000`, and explicit transaction scopes. `EvaluationTask.to_summary()` excludes `result`; `to_detail()` includes it. Derive `progress_percent` and `elapsed_seconds` from stored counters/timestamps.

- [ ] **Step 4: Ignore runtime state and run repository tests**

Add `.runtime/` to `.gitignore` if absent.

Run: `.venv/bin/python -m pytest tests/test_task_repository.py -q`

Expected: PASS.

### Task 3: FIFO service, isolated executor, and telemetry

**Files:**
- Modify: `pyproject.toml`
- Create: `src/evalhub/tasks/resources.py`
- Create: `src/evalhub/tasks/executor.py`
- Create: `src/evalhub/tasks/service.py`
- Create: `tests/test_task_service.py`
- Create: `tests/test_task_resources.py`

**Interfaces:**
- Consumes: `SQLiteTaskRepository`, `run_real_benchmark`, and progress callback.
- Produces: `EvaluationTaskService.submit/list/get/cancel/start/stop`.
- Produces: `ProcessResourceSampler.sample(pid) -> ResourceUsage` and `SubprocessEvaluationExecutor.execute(...)`.

- [ ] **Step 1: Write service lifecycle tests with fakes**

```python
def test_service_executes_pending_tasks_in_fifo_order(repository: SQLiteTaskRepository) -> None:
    executor = FakeExecutor()
    service = EvaluationTaskService(repository, executor=executor)
    first = service.submit(first_request)
    second = service.submit(second_request)
    service.run_next_for_test()
    service.run_next_for_test()
    assert executor.task_ids == [first.id, second.id]
    assert repository.get(first.id).status == "success"
    assert repository.get(second.id).status == "success"
```

Add tests proving progress/resource callbacks persist, executor errors mark failed, pending cancel skips execution, running cancel signals the executor, and terminal cancel raises `TaskConflictError`.

- [ ] **Step 2: Run service tests and confirm import failure**

Run: `.venv/bin/python -m pytest tests/test_task_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement the service state machine**

The service writes before enqueueing, owns a FIFO queue, starts one daemon scheduler thread, and exposes `run_next_for_test()` for deterministic fake-based tests. On startup it fails interrupted running records and requeues pending records. Cancellation sets an event for the active executor or directly marks a pending task canceled.

- [ ] **Step 4: Write failing resource sampler tests**

```python
def test_resource_sampler_aggregates_process_and_children() -> None:
    sampler = ProcessResourceSampler(process_factory=fake_process_factory)
    usage = sampler.sample(100)
    assert usage.cpu_percent == 75.0
    assert usage.memory_bytes == 3072
```

Add tests for disappeared processes and unavailable `nvidia-smi` returning `gpu_supported=False`.

- [ ] **Step 5: Add psutil and implement process/GPU sampling**

Add `psutil>=6.0` to runtime dependencies. Sum CPU and RSS for the root process plus recursive children. Invoke `nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits` with a short timeout; parse the busiest device and return unsupported on missing command, timeout, or malformed output.

- [ ] **Step 6: Implement spawned evaluation execution**

Use `multiprocessing.get_context("spawn")`. A top-level child function runs `run_real_benchmark(job_id=..., on_progress=...)` and writes typed progress/result/error messages. The parent monitors the message queue, samples every second, honors cancellation, terminates and joins the child, and raises typed failures for the service to persist.

- [ ] **Step 7: Run service and resource tests**

Run: `.venv/bin/python -m pytest tests/test_task_service.py tests/test_task_resources.py -q`

Expected: PASS without network or Ollama.

### Task 4: Asynchronous task HTTP API

**Files:**
- Modify: `src/evalhub/server.py`
- Create: `tests/test_task_api.py`
- Modify: `tests/test_server_frontend.py`

**Interfaces:**
- Consumes: `EvaluationTaskService`.
- Produces: `POST /api/evaluations`, `GET /api/evaluations`, `GET /api/evaluations/{id}`, and `POST /api/evaluations/{id}/cancel`.

- [ ] **Step 1: Write HTTP contract tests with a fake service**

```python
def test_create_evaluation_returns_accepted_task(api_server: ApiServer) -> None:
    response = api_server.post("/api/evaluations", evaluation_request)
    assert response.status == 202
    assert response.json["task"]["status"] == "pending"

def test_get_evaluation_detail_returns_not_found(api_server: ApiServer) -> None:
    response = api_server.get("/api/evaluations/missing")
    assert response.status == 404
    assert response.json == {"ok": False, "error": "task not found: missing"}
```

Cover list, detail, cancel, terminal conflict, malformed JSON, and preserve the synchronous route.

- [ ] **Step 2: Run API tests and confirm 404 failures**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_server_frontend.py -q`

Expected: FAIL because the task routes are unregistered.

- [ ] **Step 3: Add route parsing and lifecycle wiring**

Inject the task service through the handler class for tests. `serve()` creates `.runtime/evalhub.db`, starts the service before `serve_forever`, and stops it in `finally`. Return 202, 404, 409, and 400 with `{ok, error}` bodies as specified.

- [ ] **Step 4: Run API tests**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_server_frontend.py -q`

Expected: PASS.

### Task 5: Frontend task API and polling state

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/hooks/useEvalHub.ts`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `EvaluationTaskSummary`, `EvaluationTaskDetail`, `TaskResources`, `createEvaluation`, `getEvaluationTasks`, `getEvaluationTask`, and `cancelEvaluationTask`.
- Changes `useEvalHub.run()` to create a task immediately and exposes `tasks`, `selectedTask`, `selectTask`, `cancelTask`, and `taskError`.

- [ ] **Step 1: Write failing frontend API tests**

```typescript
it("creates an asynchronous evaluation task", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, task: pendingTask }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(createEvaluation(request)).resolves.toEqual(pendingTask);
  expect(fetchMock).toHaveBeenCalledWith("/api/evaluations", expect.objectContaining({ method: "POST" }));
});
```

Add exact tests for list, detail, and cancel paths.

- [ ] **Step 2: Run API tests and confirm missing exports**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts`

Expected: FAIL because task API functions and types do not exist.

- [ ] **Step 3: Implement task types and API functions**

Keep `runEvaluation` exported for compatibility. New create/list/detail/cancel functions unwrap the documented response envelopes using the existing `fetchJson` error conversion.

- [ ] **Step 4: Write failing App polling tests**

Mock task APIs and use fake timers to verify initial history load, immediate pending task insertion, one-second polling while active, polling stop after terminal state, detail fetch on selection, and last-known-data retention after refresh failure.

- [ ] **Step 5: Implement polling hook state**

Load tasks with the other dashboard sources. Poll only while any summary is pending/running. Select the newest task by default, fetch full detail for the selected task, and refresh that detail after list updates. `runningEvaluation` means an active task exists, not that the create request is pending.

- [ ] **Step 6: Run focused frontend state tests**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts src/App.test.tsx`

Expected: PASS for API and state behaviors added in this task.

### Task 6: Evaluation task center UI

**Files:**
- Create: `frontend/src/components/dashboard/EvaluationTaskPanel.tsx`
- Create: `frontend/src/components/dashboard/EvaluationResultDetail.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`
- Delete: `frontend/src/components/dashboard/ResultPanel.tsx`

**Interfaces:**
- Consumes: task summaries/detail and select/cancel callbacks from `useEvalHub`.
- Produces: accessible list navigation, running track, telemetry cards, and selected-result disclosure.

- [ ] **Step 1: Write failing UI behavior tests**

Assert the empty state says `尚无评测任务`; a running row exposes `3 / 5`, `60%`, elapsed time, CPU and memory; unsupported GPU says `不可用`; selecting a completed task shows `评测结果`, `4 / 5`, `0.8000`, failed examples, and collapsed raw JSON; cancel is available only for pending/running tasks.

- [ ] **Step 2: Run App tests and confirm the old result panel fails expectations**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL because the page still renders `ResultPanel` and has no task rows.

- [ ] **Step 3: Implement the task list and detail components**

Use button rows with `aria-current`, text-backed status badges, a progress element plus square cursor, and monospaced telemetry values. Keep results in `EvaluationResultDetail` and render it only when a successful selected detail has `result`.

- [ ] **Step 4: Add focused responsive styles**

Add only the running-track cursor, selected-row treatment, responsive task columns, and reduced-motion rule required by the design. Derive all colors from existing tokens.

- [ ] **Step 5: Run frontend tests, typecheck, and build**

Run:

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all commands PASS.

### Task 7: Documentation and full verification

**Files:**
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/architecture/20260804_系统架构.md`
- Modify: `docs/architecture/20260804_数据模型.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `frontend/README.md`

**Interfaces:**
- Documents the implemented endpoints, SQLite path, task lifecycle, resource semantics, recovery behavior, and frontend polling.

- [ ] **Step 1: Update documentation to match verified behavior**

Document exact request/response fields and status codes; state that one local Worker runs tasks FIFO; explain `.runtime/evalhub.db`, restart recovery, CPU/memory scope, and optional NVIDIA-only GPU telemetry.

- [ ] **Step 2: Run relevant Python tests**

Run: `.venv/bin/python -m pytest tests/test_runner.py tests/test_task_repository.py tests/test_task_service.py tests/test_task_resources.py tests/test_task_api.py tests/test_server_frontend.py -q`

Expected: PASS.

- [ ] **Step 3: Run full repository checks**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

Expected: all commands PASS without warnings or generated runtime artifacts.

- [ ] **Step 4: Review task-scoped diff and repository hygiene**

Run: `git status --short` and `git diff --stat`.

Confirm only task files plus the user's pre-existing changes are present, `.runtime/` and frontend build output are ignored, and no secrets, logs, databases, or unrelated formatting appear.
