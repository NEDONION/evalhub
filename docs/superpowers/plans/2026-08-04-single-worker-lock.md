# SQLite Single Worker Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent multiple EvalHub worker processes from recovering and consuming the same SQLite task database.

**Architecture:** Acquire a non-blocking OS file lock before task recovery and hold it for the worker lifetime. Reject a second service before it can mutate persisted task state, then release the lock only after the worker exits.

**Tech Stack:** Python 3.11, `fcntl`, SQLite, pytest.

## Global Constraints

- Keep the existing SQLite schema and public task API unchanged.
- Add no third-party dependency.
- Preserve unrelated working-tree changes.
- Follow the repository's Chinese docstring and comment requirements.

---

### Task 1: Enforce one Worker per SQLite database

**Files:**
- Modify: `src/evalhub/tasks/service.py`
- Test: `tests/test_task_service.py`
- Modify: `docs/architecture/20260804_系统架构.md`

**Interfaces:**
- Consumes: `SQLiteTaskRepository.database_path` and `EvaluationTaskService.start()` / `stop()`.
- Produces: an exclusive `<database>.worker.lock` lifecycle owned by the active service.

- [ ] **Step 1: Add the failing regression test**

```python
def test_service_allows_only_one_worker_per_database(repository):
    first = EvaluationTaskService(repository, executor=RecordingExecutor())
    second = EvaluationTaskService(repository, executor=RecordingExecutor())
    first.start()
    try:
        with pytest.raises(RuntimeError, match="worker is already running"):
            second.start()
    finally:
        first.stop()
    second.start()
    second.stop()
```

- [ ] **Step 2: Verify the test fails against the pre-fix code**

Run: `.venv/bin/python -m pytest tests/test_task_service.py::test_service_allows_only_one_worker_per_database -q`

Expected: FAIL because the second service starts without raising `RuntimeError`.

- [ ] **Step 3: Implement the minimal lock lifecycle**

Use `fcntl.flock(..., LOCK_EX | LOCK_NB)` before `recover_running_nodes()`. Close the file on contention or startup failure; unlock and close it after the worker exits.

- [ ] **Step 4: Verify the regression and service suite**

Run: `.venv/bin/python -m pytest tests/test_task_service.py -q`

Expected: all service tests pass.

- [ ] **Step 5: Run repository verification**

Run: `.venv/bin/python -m ruff check .`

Run: `.venv/bin/python -m pytest`

Run: `git diff --check`

Expected: every command exits with status 0.
