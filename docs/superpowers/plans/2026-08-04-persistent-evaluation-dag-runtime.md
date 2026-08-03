# Persistent Evaluation DAG Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the current SQLite-backed evaluation task center into a persistent node runtime with sample checkpoints, retry/audit APIs, suite execution, capability aggregation, and a Chinese node-detail UI with a six-axis radar chart.

**Architecture:** Keep `EvaluationTaskService` as the single FIFO owner and replace only its default black-box executor with a repository-backed workflow executor. The existing subprocess evaluator remains the isolation boundary for runnable native Benchmarks; node snapshots, append-only events, and sample results live beside `evaluation_tasks` in the same SQLite database. React continues to poll task details and lazily loads the selected node, while the capability chart renders the persisted `capability_aggregate` output.

**Tech Stack:** Python 3.11 standard-library `sqlite3`, dataclasses, multiprocessing/threading, pytest, React 19, TypeScript 7, Tailwind CSS 4, Vitest, Testing Library, inline SVG for the data chart, Lucide icons for controls.

## Global Constraints

- Continue using the existing `.runtime/evalhub.db`, WAL mode, `busy_timeout=5000`, and one local Worker.
- Do not add SQLAlchemy, Alembic, Celery, Redis, a workflow engine, or a chart dependency.
- Keep `/api/evaluations/run` and the CLI behavior compatible.
- Every Benchmark defaults to all samples; quick/custom limits remain explicit user choices.
- Persist each successful sample before advancing progress and skip it after restart or retry.
- Missing executors and failed Benchmarks are unassessed, never zero-scored.
- Do not execute HumanEval or MBPP on the host without a sandbox.
- Keep the current Chinese blue-white UI, compact spacing, accessible controls, and mobile layout.
- Unit tests must not access the network, download datasets, or start Ollama.
- Do not stage unrelated modified documentation or runtime files.

---

### Task 1: Versioned Benchmark Registry and Capability Aggregator

**Files:**
- Create: `src/evalhub/benchmarks/__init__.py`
- Create: `src/evalhub/benchmarks/models.py`
- Create: `src/evalhub/benchmarks/registry.py`
- Create: `src/evalhub/benchmarks/aggregation.py`
- Create: `tests/test_benchmark_registry.py`
- Create: `tests/test_capability_aggregation.py`

**Interfaces:**
- Produces: `BenchmarkSpec`, `BenchmarkSuiteSpec`, `Capability`, `ExecutorKind`, and `NormalizationKind`.
- Produces: `benchmark_registry()`, `suite_registry()`, `get_benchmark_spec(id)`, and `get_suite_spec(id)`.
- Produces: `aggregate_capability_profile(suite, benchmark_outputs) -> dict[str, object]`.

- [ ] **Step 1: Add failing Registry tests**

```python
def test_core_suite_has_six_capabilities_and_real_sources() -> None:
    suite = get_suite_spec("llm-industry-core-v1")
    specs = [get_benchmark_spec(item) for item in suite.benchmark_ids]
    assert {item.capability for item in specs} == set(Capability)
    assert all(item.dataset_source and item.dataset_revision for item in specs)
    assert all(item.weight > 0 for item in specs)

def test_generation_config_is_immutable() -> None:
    spec = get_benchmark_spec("gsm8k")
    with pytest.raises(TypeError):
        spec.generation_config["temperature"] = 1
```

- [ ] **Step 2: Run the Registry tests and confirm the package is missing**

Run: `.venv/bin/python -m pytest tests/test_benchmark_registry.py -q`
Expected: FAIL with `ModuleNotFoundError: evalhub.benchmarks`.

- [ ] **Step 3: Add immutable declarations for the industry Registry**

Use `MappingProxyType` for `generation_config`, include the six fixed capability IDs, and register real upstream sources for MMLU-Pro, MMLU, IFEval, GSM8K, MATH-500, BBH, ARC-Challenge, MuSR, HellaSwag, HumanEval, MBPP, TruthfulQA, and BBQ. Register `llm-industry-core-v1` and preserve stable ordering.

- [ ] **Step 4: Add failing normalization and partial-profile tests**

```python
def test_chance_corrected_score_uses_random_baseline() -> None:
    assert normalize_score(0.625, NormalizationKind.CHANCE_CORRECTED, 0.25) == 50.0

def test_partial_profile_keeps_missing_axes_unassessed() -> None:
    profile = aggregate_capability_profile(core_suite, [gsm8k_success])
    assert profile["capabilities"]["mathematics"]["score"] == 80.0
    assert profile["capabilities"]["knowledge"]["score"] is None
    assert profile["capabilities"]["knowledge"]["status"] == "unassessed"
```

- [ ] **Step 5: Implement normalization and weighted aggregation**

Implement scale-to-100 and chance-corrected normalization. Return all six axes with `score`, `status`, `coverage`, and `benchmark_results`; include suite/model metadata and success/failed/blocked counts.

- [ ] **Step 6: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_benchmark_registry.py tests/test_capability_aggregation.py -q`
Expected: PASS.

---

### Task 2: Persistent Node, Event, and Sample Repository

**Files:**
- Modify: `src/evalhub/tasks/models.py`
- Modify: `src/evalhub/tasks/repository.py`
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `src/evalhub/tasks/__init__.py`
- Modify: `tests/test_task_repository.py`
- Create: `tests/test_workflow_repository.py`

**Interfaces:**
- Produces: `EvaluationNode`, `EvaluationNodeEvent`, `EvaluationSampleCheckpoint`, `WorkflowNodeSpec`, and `NodeStatus`.
- Produces repository methods `create_with_nodes`, `list_nodes`, `get_node`, `start_node`, `complete_node`, `fail_node`, `block_node`, `retry_node`, `recover_running_nodes`, `record_sample`, `successful_sample_keys`, `list_samples`, and `cancel_nodes`.
- Produces: `node_summary(node)` and `node_detail(node, events, samples)`.

- [ ] **Step 1: Write failing schema and transaction tests**

```python
def test_create_with_nodes_persists_task_graph_atomically(tmp_path: Path) -> None:
    repository = SQLiteTaskRepository(tmp_path / "evalhub.db")
    task = repository.create_with_nodes(request, workflow_specs)
    assert [node.node_key for node in repository.list_nodes(task.id)] == expected_keys

def test_transition_and_event_are_committed_together(repository) -> None:
    node = repository.start_node(task.id, "benchmark:gsm8k")
    event = repository.list_node_events(node.id)[-1]
    assert (event.from_status, event.to_status, event.attempt) == ("pending", "running", 1)
```

- [ ] **Step 2: Run repository tests and confirm missing models/methods**

Run: `.venv/bin/python -m pytest tests/test_workflow_repository.py -q`
Expected: FAIL because workflow persistence does not exist.

- [ ] **Step 3: Add the three additive SQLite tables**

Create `evaluation_nodes`, `evaluation_node_events`, and `evaluation_sample_results` using `CREATE TABLE IF NOT EXISTS`; add foreign keys and indexes for `(task_id, status)`, `(node_id, created_at)`, and `(node_id, sample_index)`. Enable `PRAGMA foreign_keys=ON` on every connection.

- [ ] **Step 4: Implement atomic node transitions and append-only events**

Every transition method must validate the current state, update elapsed time/checkpoint/output/error fields, and insert one event in the same connection context. `retry_node` accepts only `failed`/`blocked`, resets descendants to `pending`, preserves events, and keeps successful sample rows.

- [ ] **Step 5: Add failing sample resume tests**

```python
def test_record_sample_and_checkpoint_are_atomic(repository) -> None:
    repository.record_sample(node.id, sample_result, completed=1, total=2)
    assert repository.successful_sample_keys(node.id) == {"sample-1"}
    assert repository.get_node(node.id).completed_samples == 1

def test_duplicate_sample_is_upserted_not_counted_twice(repository) -> None:
    repository.record_sample(node.id, sample_result, completed=1, total=1)
    repository.record_sample(node.id, sample_result, completed=1, total=1)
    assert len(repository.list_samples(node.id, limit=50).items) == 1
```

- [ ] **Step 6: Implement checkpoint persistence and cursor pagination**

Use `(node_id, sample_key)` as the unique identity. Return stable `sample_index, sample_key` cursor ordering; enforce default `limit=50` and maximum `limit=200`.

- [ ] **Step 7: Run all repository tests**

Run: `.venv/bin/python -m pytest tests/test_task_repository.py tests/test_workflow_repository.py -q`
Expected: PASS.

---

### Task 3: Sample-Aware Subprocess Execution

**Files:**
- Modify: `src/evalhub/engine/runner.py`
- Modify: `src/evalhub/engine/__init__.py`
- Modify: `src/evalhub/cli.py`
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_task_executor.py`

**Interfaces:**
- Produces: `SampleResultCallback = Callable[[EvaluationSampleResult, int, int], None]`.
- Extends: `EvaluationRunner.run(..., skip_sample_ids=frozenset(), on_sample_result=None)`.
- Extends: `SubprocessEvaluationExecutor.execute(..., skip_sample_ids=frozenset(), on_sample_result=None)` without breaking existing callers.

- [ ] **Step 1: Add failing Runner checkpoint tests**

```python
def test_runner_skips_completed_samples_and_reports_new_results() -> None:
    emitted = []
    results, _ = runner.run(
        job=job,
        benchmark=benchmark,
        samples=samples,
        skip_sample_ids={samples[0].id},
        on_sample_result=lambda result, completed, total: emitted.append(result.sample_id),
    )
    assert emitted == [samples[1].id]
```

- [ ] **Step 2: Implement callback ordering and skip behavior**

Invoke `on_sample_result` only after scoring produces an immutable result. Progress counts persisted skipped samples plus newly completed samples and never exceeds the original sample total.

- [ ] **Step 3: Add failing child-process sample-event tests**

Assert `_evaluation_process` emits `sample_result` messages containing the sample ID, input, prediction, reference, metric, score, and reason before its final result message.

- [ ] **Step 4: Extend subprocess messaging**

Pass skipped sample IDs into the child, serialize sample-result events, and invoke the parent callback. Preserve existing progress/resource/cancel behavior and the old call signature defaults.

- [ ] **Step 5: Run focused executor tests**

Run: `.venv/bin/python -m pytest tests/test_runner.py tests/test_task_executor.py -q`
Expected: PASS.

---

### Task 4: Persistent Workflow Executor, Recovery, and Retry

**Files:**
- Create: `src/evalhub/tasks/workflow.py`
- Create: `src/evalhub/tasks/runtime.py`
- Modify: `src/evalhub/tasks/service.py`
- Modify: `src/evalhub/tasks/models.py`
- Create: `tests/test_workflow_runtime.py`
- Modify: `tests/test_task_service.py`

**Interfaces:**
- Produces: `build_workflow(request: TaskRequest) -> tuple[WorkflowNodeSpec, ...]`.
- Produces: `classify_runtime_error(exc) -> Literal["transient", "blocked", "failed"]`.
- Produces: `PersistentWorkflowExecutor.execute(...) -> dict[str, object]` implementing the current `TaskExecutor` protocol.
- Extends service with `list_nodes`, `get_node`, `list_node_samples`, and `retry_node`.
- Extends `TaskRequest` with `suite_id: str | None = None`; legacy `dataset` remains required for stored-request compatibility and is ignored when `suite_id` is set.

- [ ] **Step 1: Write failing graph-generation tests**

```python
def test_suite_builds_prepare_benchmark_aggregate_finalize_graph() -> None:
    graph = build_workflow(suite_request)
    assert graph[0].node_key == "prepare_assets"
    assert "benchmark:gsm8k" in {node.node_key for node in graph}
    assert graph[-2].node_key == "capability_aggregate"
    assert graph[-1].node_key == "workflow_finalize"
```

- [ ] **Step 2: Implement deterministic graph generation**

When `suite_id` is present, create one Benchmark node per suite member. Legacy requests map `dataset` to a single-Benchmark graph. Freeze model, adapter, source revision, generation config, subject, sample mode, and limit in node `input_json`.

- [ ] **Step 3: Write failing runtime tests with a fake Benchmark executor**

Cover successful flow, one blocked Benchmark with partial profile, transient retry exactly three attempts, deterministic blocked errors, cancel, and restart resume that skips persisted samples.

- [ ] **Step 4: Implement the single-Worker runtime loop**

Process only the current FIFO task. Ordinary dependencies require success; aggregate/finalize nodes use terminal barriers. Native `gsm8k` and `mmlu` call the subprocess executor; unavailable `lm_eval` and sandbox executors become `blocked` with explicit codes. Build Benchmark output from persisted samples, then aggregate capabilities from successful node outputs.

- [ ] **Step 5: Integrate recovery and top-level lifecycle**

On service start, reset interrupted nodes to `pending` while attempts remain, keep the top task resumable, and enqueue pending/running tasks in FIFO order. The finalizer returns the top-level result; failed/blocked required Benchmarks mark the task failed while leaving the capability node output readable.

- [ ] **Step 6: Implement manual node retry**

Reopen a failed top task, reset the selected node and descendants, enqueue the task once, and reject running/success/canceled nodes with `TaskConflictError`. Preserve successful Benchmark samples by default.

- [ ] **Step 7: Run runtime and service tests**

Run: `.venv/bin/python -m pytest tests/test_workflow_runtime.py tests/test_task_service.py -q`
Expected: PASS.

---

### Task 5: Benchmark, Suite, Node, Retry, and Sample APIs

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `tests/test_task_api.py`
- Create: `tests/test_benchmark_api.py`

**Interfaces:**
- Produces: `GET /api/benchmarks` and `GET /api/suites`.
- Extends: `GET /api/evaluations/{task_id}` with `nodes` summaries.
- Produces: `GET /api/evaluations/{task_id}/nodes/{node_id}`.
- Produces: `GET /api/evaluations/{task_id}/nodes/{node_id}/samples?status=&cursor=&limit=`.
- Produces: `POST /api/evaluations/{task_id}/nodes/{node_id}/retry`.

- [ ] **Step 1: Add failing route-contract tests**

```python
def test_get_task_detail_contains_node_summaries(api_server) -> None:
    response = api_server.get(f"/api/evaluations/{task.id}")
    assert response.status == 200
    assert response.json["task"]["nodes"][0]["status"] == "pending"

def test_retry_running_node_returns_conflict(api_server) -> None:
    response = api_server.post(f"/api/evaluations/{task.id}/nodes/{node.id}/retry")
    assert response.status == 409
```

- [ ] **Step 2: Add exact path parsers and stable envelopes**

Parse task/node IDs without accepting extra path segments. Return `{ok, node}`, `{items, next_cursor}`, and structured 400/404/409 errors. Validate sample status, cursor, and limit at the HTTP boundary.

- [ ] **Step 3: Add Registry readiness responses**

Expose executor readiness and local dataset preparation status. Registry metadata is factual even when an executor is unavailable; unsupported items return `ready: false` and an explicit reason.

- [ ] **Step 4: Run API tests**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_benchmark_api.py -q`
Expected: PASS.

---

### Task 6: Frontend Runtime Data Layer

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/hooks/useEvalHub.ts`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces frontend types `EvaluationNodeSummary`, `EvaluationNodeDetail`, `EvaluationNodeEvent`, `CapabilityProfile`, `BenchmarkOption`, and `SuiteOption`.
- Produces API functions `getBenchmarks`, `getSuites`, `getEvaluationNode`, `getEvaluationNodeSamples`, and `retryEvaluationNode`.
- Extends `useEvalHub` with selected-node state, lazy node loading, and retry actions.

- [ ] **Step 1: Add failing API tests for exact URLs and methods**

```typescript
it("loads one workflow node", async () => {
  await getEvaluationNode("job 1", "benchmark:gsm8k");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/evaluations/job%201/nodes/benchmark%3Agsm8k",
    expect.anything(),
  );
});
```

- [ ] **Step 2: Implement typed API calls and envelopes**

Keep existing task APIs unchanged. Add cursor query construction with `URLSearchParams` and expose capability output as a typed nullable field on node details.

- [ ] **Step 3: Add failing hook/UI-state tests**

Verify selecting a task selects its first node, selecting a node loads its detail, polling refreshes an active selected node, retry refreshes task/node state, and a node API failure keeps the last successful task data visible.

- [ ] **Step 4: Implement lazy selected-node state**

Store `selectedNodeId`, `selectedNode`, and `nodeError`; reset them only when the selected task changes. Poll node detail only while the task is active and refetch immediately after retry.

- [ ] **Step 5: Run frontend data tests**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts src/App.test.tsx`
Expected: PASS.

---

### Task 7: Current-UI Node Inspector and Capability Radar

**Files:**
- Create: `frontend/src/components/dashboard/CapabilityRadar.tsx`
- Create: `frontend/src/components/dashboard/EvaluationNodeInspector.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationTaskPanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: selected task/node state and callbacks from `useEvalHub`.
- Produces: compact node navigation, node timeline, effective-config/output disclosure, retry control, and accessible six-axis capability visualization.

- [ ] **Step 1: Add failing component behavior tests**

Assert Chinese labels for node states, accumulated duration, `第 2 / 3 次`, error message, audit events, retry visibility, and `部分完成`. Assert all six capability names exist and an unassessed axis renders `未评测`, not `0`.

- [ ] **Step 2: Implement compact node navigation inside the task detail**

Use button rows with Lucide status icons, `aria-current`, fixed status/attempt columns, and a responsive two-line mobile layout. Keep the existing task list and telemetry unchanged.

- [ ] **Step 3: Add Suite and single-Benchmark targeting to the current form**

Use a two-option segmented control for “评测套件 / 单项 Benchmark”, populated from the Registry APIs. Keep “全部样本” selected by default, show readiness and coverage truthfully, and continue sending the selected legacy `dataset` alongside optional `suite_id` for backward-compatible persistence.

- [ ] **Step 4: Implement the node inspector**

Render status, sample progress, accumulated duration, attempt count, error, structured timeline, and collapsible JSON config/output. Show one icon+text retry button only for failed/blocked nodes.

- [ ] **Step 5: Implement the six-axis radar without a dependency**

Use a fixed `viewBox`, six axis lines, grid polygons, and one data polygon generated from numeric scores. Omit points for `null` axes and show the same values in an adjacent semantic table. Do not use gradients or decorative effects.

- [ ] **Step 6: Add only focused responsive styles**

Reuse current color tokens and border treatments. Stabilize chart aspect ratio, prevent labels from clipping, and preserve `prefers-reduced-motion` behavior.

- [ ] **Step 7: Run frontend tests and production build**

Run: `npm --prefix frontend run test:run`
Expected: PASS.

Run: `npm --prefix frontend run build`
Expected: PASS.

---

### Task 8: End-to-End Verification and Documentation Alignment

**Files:**
- Verify: `docs/superpowers/specs/2026-08-04-persistent-evaluation-dag-runtime-design.md`
- Verify: `docs/superpowers/plans/2026-08-04-persistent-evaluation-dag-runtime.md`

**Interfaces:**
- Verifies the complete backend/frontend contract and local UI behavior.

- [ ] **Step 1: Run Python quality and full tests**

Run: `.venv/bin/python -m ruff check src tests`
Expected: PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 2: Run frontend quality checks**

Run: `npm --prefix frontend run typecheck`
Expected: PASS.

Run: `npm --prefix frontend run test:run`
Expected: PASS.

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 3: Start the integrated local server and verify APIs**

Run: `./scripts/start_local.sh`
Expected: server reports one local URL, Ollama readiness truthfully, and no duplicate Worker.

Create an Oracle single-Benchmark task through `POST /api/evaluations`, then verify task detail contains the four-node graph, the Benchmark has persisted samples, audit events are ordered, and the capability profile has mathematics assessed with the other missing axes unassessed.

- [ ] **Step 4: Verify the UI in desktop and mobile viewports**

Use Playwright at 1440x1000 and 390x844. Confirm the task list, node list, timeline, retry button, radar polygon, and six-axis table are visible without overlap or horizontal scrolling. Confirm the plotted values match the node API.

- [ ] **Step 5: Review and commit only related files**

Run: `git diff --check`
Expected: no output.

Inspect `git status`, exclude unrelated user documentation changes, and commit the Runtime/backend/frontend implementation with a focused message.
