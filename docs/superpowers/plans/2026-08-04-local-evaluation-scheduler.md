# Local Evaluation Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent local evaluation scheduling with one-time, interval, and Cron triggers, reliable run history, and a Chinese scheduling center while preserving a distributed migration boundary.

**Architecture:** Persist schedule definitions and run history in SQLite, rebuild APScheduler jobs at service startup, and dispatch immutable evaluation-request snapshots through a bounded single-worker queue. Keep scheduler, repository, dispatcher, and evaluation service behind protocols so Celery/Redis, PostgreSQL, or Temporal can replace local implementations without changing APIs.

**Tech Stack:** Python 3.11+, SQLite via `sqlite3`, APScheduler 3.x, standard-library queue/threading/HTTP, vanilla HTML/CSS/JavaScript, pytest/unittest.

## Global Constraints

- Local startup must not require Redis or PostgreSQL.
- The default timezone is exactly `Asia/Shanghai`; timestamps persist as UTC.
- Scheduled requests reference Registry model/Benchmark/Suite IDs and cannot contain shell commands or Python callables.
- Every trigger creates a distinct auditable ScheduleRun linked to an Evaluation Job.
- The default sample mode remains complete-dataset execution.
- The default is `max_instances=1`, `coalesce=true`, and `misfire_grace_time=300` seconds.
- `schedule_id + scheduled_for` is a unique idempotency key.
- Deleting or pausing a schedule affects future triggers only and retains history.

---

### Task 1: Scheduler Domain and Trigger Validation

**Files:**
- Modify: `pyproject.toml`
- Create: `src/evalhub/scheduler/__init__.py`
- Create: `src/evalhub/scheduler/models.py`
- Create: `src/evalhub/scheduler/triggers.py`
- Test: `tests/test_scheduler_triggers.py`

**Interfaces:**
- Consumes: suite `EvaluationRequest` JSON shape from the capability plan.
- Produces: `ScheduleState`, `ScheduleRunStatus`, `TriggerType`, `EvaluationSchedule`, `ScheduleRun`, `TriggerValidationError`, `validate_trigger()`, and `next_run_time()`.

- [ ] **Step 1: Write failing timezone and trigger tests**

```python
def test_once_trigger_rejects_past_local_time() -> None:
    with pytest.raises(TriggerValidationError, match="future"):
        validate_trigger("once", {"run_at": "2026-08-03T10:00:00"}, "Asia/Shanghai", now=NOW)


def test_cron_calculates_next_time_in_explicit_timezone() -> None:
    result = next_run_time("cron", {"expression": "0 9 * * 1-5"}, "Asia/Shanghai", now=NOW)
    assert result.tzinfo is UTC
    assert result > NOW
```

- [ ] **Step 2: Run and verify missing scheduler modules**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_triggers.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'evalhub.scheduler'`.

- [ ] **Step 3: Add the pinned scheduler dependency**

```toml
[project.optional-dependencies]
scheduler = ["APScheduler>=3.10,<4"]
```

- [ ] **Step 4: Install the scheduler development environment**

Run: `.venv/bin/python -m pip install -e ".[dev,scheduler]"`

Expected: installation exits 0 and `.venv/bin/python -c "import apscheduler"` exits 0.

- [ ] **Step 5: Implement immutable trigger and schedule models**

```python
class TriggerType(StrEnum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduleState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    DELETED = "deleted"


@dataclass(frozen=True)
class EvaluationSchedule:
    id: str
    name: str
    description: str
    state: ScheduleState
    trigger_type: TriggerType
    timezone: str
    trigger_config: dict[str, object]
    evaluation_request: dict[str, object]
    timeout_seconds: int = 3600
    max_retries: int = 0
    retry_delay_seconds: int = 60
    max_concurrency: int = 1
    coalesce: bool = True
    misfire_grace_time: int = 300
    version: int = 1
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class ScheduleRun:
    id: str
    schedule_id: str
    schedule_version: int
    scheduled_for: datetime
    triggered_at: datetime | None
    finished_at: datetime | None
    status: ScheduleRunStatus
    attempt: int
    idempotency_key: str
    evaluation_request: dict[str, object]
    evaluation_job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
```

Define `ScheduleRunStatus` with exact string values `queued`, `running`, `success`, `partial`, `failed`, `missed`, and `canceled`.

- [ ] **Step 6: Validate once, interval, and five-field Cron triggers**

Use `zoneinfo.ZoneInfo` for timezone validation and APScheduler `CronTrigger.from_crontab(expression, timezone=zone)` for Cron semantics. Interval accepts one positive `seconds` integer. Convert every returned next-run timestamp to UTC.

- [ ] **Step 7: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_triggers.py -q`

Expected: PASS.

- [ ] **Step 8: Commit domain and triggers**

```bash
git add pyproject.toml src/evalhub/scheduler tests/test_scheduler_triggers.py
git commit -m "feat: define scheduler domain and triggers"
```

### Task 2: Transactional SQLite Schedule Repository

**Files:**
- Create: `src/evalhub/scheduler/repository.py`
- Test: `tests/test_schedule_repository.py`

**Interfaces:**
- Consumes: scheduler domain dataclasses.
- Produces: `ScheduleRepository` protocol and `SQLiteScheduleRepository` with `create`, `get`, `list`, `update`, `soft_delete`, `create_run`, `update_run`, `get_run`, `list_runs`, and `recover_stale_runs`.

```python
class ScheduleRepository(Protocol):
    def create(self, schedule: EvaluationSchedule) -> EvaluationSchedule: ...
    def get(self, schedule_id: str) -> EvaluationSchedule: ...
    def list(self, *, include_deleted: bool = False) -> list[EvaluationSchedule]: ...
    def update(self, schedule: EvaluationSchedule, *, expected_version: int) -> EvaluationSchedule: ...
    def soft_delete(self, schedule_id: str, *, expected_version: int) -> EvaluationSchedule: ...
    def create_run(self, run: ScheduleRun) -> ScheduleRun: ...
    def update_run(self, run: ScheduleRun) -> ScheduleRun: ...
    def list_runs(self, *, schedule_id: str | None = None, status: str | None = None) -> list[ScheduleRun]: ...
```

- [ ] **Step 1: Write failing persistence, optimistic-lock, and idempotency tests**

```python
def test_repository_restores_schedule_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "evalhub.db"
    first = SQLiteScheduleRepository(path)
    first.create(schedule())
    first.close()
    assert SQLiteScheduleRepository(path).get("schedule_1").name == "Nightly core"


def test_create_run_is_idempotent_for_scheduled_time(repo) -> None:
    first = repo.create_run(run(scheduled_for=NOW))
    second = repo.create_run(run(id="different", scheduled_for=NOW))
    assert second.id == first.id
```

- [ ] **Step 2: Run and verify missing repository failures**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_schedule_repository.py -q`

Expected: FAIL importing `SQLiteScheduleRepository`.

- [ ] **Step 3: Create the schema transactionally**

Use `sqlite3.connect(path, check_same_thread=False)`, `PRAGMA foreign_keys=ON`, WAL mode, a process-local `RLock`, and explicit transactions. Create `schedules` and `schedule_runs`; store trigger/evaluation JSON with sorted keys; enforce `UNIQUE(schedule_id, scheduled_for)` and a foreign key from runs to schedules.

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_run_idempotency
ON schedule_runs(schedule_id, scheduled_for);
```

- [ ] **Step 4: Implement optimistic updates and soft deletion**

Update with `WHERE id = ? AND version = ?`, increment version, and raise `ScheduleConflictError` when `rowcount != 1`. Soft delete sets `state='deleted'` and `deleted_at`; list excludes deleted rows unless `include_deleted=True`.

```python
cursor = connection.execute(
    "UPDATE schedules SET payload = ?, version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
    (payload, utc_now().isoformat(), schedule.id, expected_version),
)
if cursor.rowcount != 1:
    raise ScheduleConflictError(schedule.id)
```

- [ ] **Step 5: Implement stale-run recovery**

`recover_stale_runs(cutoff)` changes `running` records with `triggered_at < cutoff` to `failed`, sets `error_code='worker_lost'`, and preserves prior timestamps and request snapshots.

```python
connection.execute(
    "UPDATE schedule_runs SET status='failed', error_code='worker_lost', finished_at=? "
    "WHERE status='running' AND triggered_at < ?",
    (utc_now().isoformat(), cutoff.isoformat()),
)
```

- [ ] **Step 6: Run repository tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_schedule_repository.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the repository**

```bash
git add src/evalhub/scheduler/repository.py tests/test_schedule_repository.py
git commit -m "feat: persist schedules and run history"
```

### Task 3: Bounded Evaluation Job Dispatcher

**Files:**
- Create: `src/evalhub/scheduler/dispatcher.py`
- Test: `tests/test_schedule_dispatcher.py`

**Interfaces:**
- Consumes: `SQLiteScheduleRepository`, `ScheduleRun`, and `SuiteEvaluationService.run()`.
- Produces: `JobDispatcher` protocol and `LocalJobDispatcher.start()`, `stop()`, `dispatch(request, trigger_id)`, `queue_size`, and `running_count`.

- [ ] **Step 1: Write failing queue, status, and retry tests**

```python
def test_dispatcher_records_successful_evaluation(repo) -> None:
    dispatcher = LocalJobDispatcher(repo, evaluator=lambda request: {"status": "success", "job_id": "job_1"})
    dispatcher.run_one(run_id="run_1")
    stored = repo.get_run("run_1")
    assert stored.status == ScheduleRunStatus.SUCCESS
    assert stored.evaluation_job_id == "job_1"


def test_dispatcher_retries_then_records_final_failure(repo) -> None:
    evaluator = FailingEvaluator()
    dispatcher = LocalJobDispatcher(repo, evaluator=evaluator, clock=fake_clock)
    dispatcher.run_one(run_id="run_1")
    assert evaluator.calls == 2
    assert repo.get_run("run_1").status == ScheduleRunStatus.FAILED
```

- [ ] **Step 2: Run and verify missing dispatcher failures**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_schedule_dispatcher.py -q`

Expected: FAIL importing `LocalJobDispatcher`.

- [ ] **Step 3: Implement the protocol and bounded local queue**

Use `queue.Queue(maxsize=32)`, one daemon worker thread, and a stop event. `dispatch()` stores the immutable request snapshot before enqueueing. Queue-full returns `DispatcherFullError` and records a failed run with `error_code='queue_full'`.

```python
class JobDispatcher(Protocol):
    def dispatch(self, request: dict[str, object], *, trigger_id: str) -> str: ...
```

- [ ] **Step 4: Implement timeout and retry accounting**

Each attempt updates `attempt`, sets `running`, invokes the suite service, and maps suite `success/partial/failed` to ScheduleRun status. Apply fixed retry delay through an injected sleeper; production uses `Event.wait(delay)` so shutdown can interrupt waiting. Store sanitized exception class and message.

```python
for attempt in range(1, run.max_retries + 2):
    self.repository.update_run(replace(run, status=ScheduleRunStatus.RUNNING, attempt=attempt))
    try:
        result = self.evaluator(run.evaluation_request)
        return self._complete_from_result(run, result, attempt)
    except Exception as exc:
        if attempt > run.max_retries:
            return self._fail(run, exc, attempt)
        self.stop_event.wait(run.retry_delay_seconds)
```

- [ ] **Step 5: Run dispatcher and repository tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_schedule_dispatcher.py tests/test_schedule_repository.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the dispatcher**

```bash
git add src/evalhub/scheduler/dispatcher.py tests/test_schedule_dispatcher.py
git commit -m "feat: dispatch scheduled evaluation jobs"
```

### Task 4: APScheduler Service, Misfires, and Restart Recovery

**Files:**
- Create: `src/evalhub/scheduler/service.py`
- Test: `tests/test_scheduler_service.py`

**Interfaces:**
- Consumes: trigger helpers, repository, and dispatcher.
- Produces: `LocalSchedulerService.start()`, `shutdown()`, `create_schedule()`, `update_schedule()`, `pause()`, `resume()`, `delete()`, `trigger_now()`, and `health()`.

- [ ] **Step 1: Write failing idempotency, overlap, and recovery tests**

```python
def test_duplicate_callback_creates_one_run(service, repo) -> None:
    service.handle_trigger("schedule_1", scheduled_for=NOW)
    service.handle_trigger("schedule_1", scheduled_for=NOW)
    assert len(repo.list_runs(schedule_id="schedule_1")) == 1


def test_start_rebuilds_active_jobs_only(service, repo) -> None:
    repo.create(active_schedule())
    repo.create(paused_schedule())
    service.start()
    assert service.registered_schedule_ids() == {"active_1"}
```

- [ ] **Step 2: Run and verify service failures**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_service.py -q`

Expected: FAIL importing `LocalSchedulerService`.

- [ ] **Step 3: Configure BackgroundScheduler with explicit defaults**

```python
self._scheduler = BackgroundScheduler(
    timezone=ZoneInfo("Asia/Shanghai"),
    job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300},
)
```

Use deterministic APScheduler job IDs `schedule:<schedule_id>`. Rebuild active jobs from SQLite before starting. Repository remains the source of truth; APScheduler's in-memory job store is disposable.

- [ ] **Step 4: Implement trigger handling and misfire records**

`handle_trigger()` first inserts the idempotent ScheduleRun, checks whether another run for the schedule is queued/running, records `missed` on overlap or expired grace, and dispatches otherwise. A once schedule changes to `paused` after successful enqueue.

- [ ] **Step 5: Implement lifecycle and health**

`start()` recovers stale runs older than the configured lease, starts dispatcher and scheduler, and reports `running`, queue length, running count, active schedules, and latest error. `shutdown()` stops accepting triggers, shuts down APScheduler without waiting for future jobs, then drains/stops the dispatcher with a bounded timeout.

```python
def shutdown(self) -> None:
    self._accepting = False
    if self._scheduler.running:
        self._scheduler.shutdown(wait=False)
    self._dispatcher.stop(timeout=10.0)
```

- [ ] **Step 6: Run scheduler unit tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_service.py tests/test_scheduler_triggers.py -q`

Expected: PASS without real-time sleeps.

- [ ] **Step 7: Commit the scheduler service**

```bash
git add src/evalhub/scheduler/service.py tests/test_scheduler_service.py
git commit -m "feat: schedule persistent evaluation triggers"
```

### Task 5: Scheduler HTTP API and Server Lifecycle

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `src/evalhub/cli.py:197-201`
- Test: `tests/test_scheduler_api.py`

**Interfaces:**
- Consumes: `LocalSchedulerService` and suite request validation.
- Produces: schedule CRUD, pause/resume/trigger, run history, scheduler health, and startup/shutdown integration.

- [ ] **Step 1: Write failing API tests**

```python
def test_create_schedule_defaults_to_full_dataset_and_shanghai(client) -> None:
    response = client.post_json("/api/schedules", schedule_payload_without_defaults())
    assert response["schedule"]["timezone"] == "Asia/Shanghai"
    assert response["schedule"]["evaluation_request"]["sample_mode"] == "all"


def test_update_rejects_stale_version(client) -> None:
    response = client.put_json("/api/schedules/schedule_1", {"version": 0, "name": "stale"})
    assert response.status == 409
```

- [ ] **Step 2: Run and verify 404 failures**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_api.py -q`

Expected: FAIL with HTTP 404 for `/api/schedules`.

- [ ] **Step 3: Add exact schedule routes**

Implement:

```text
GET/POST /api/schedules
GET/PUT/DELETE /api/schedules/{id}
POST /api/schedules/{id}/pause
POST /api/schedules/{id}/resume
POST /api/schedules/{id}/trigger
GET /api/schedule-runs
GET /api/scheduler/health
```

Return 400 for validation, 404 for unknown IDs, 409 for version conflicts, and 503 when the scheduler is not running. Serialize all datetimes as ISO 8601 with offsets.

- [ ] **Step 4: Integrate service lifecycle with `serve()`**

Create `.runtime/evalhub.db`, construct the repository/dispatcher/scheduler before `serve_forever()`, start after the TCP server binds, and call `shutdown()` in the existing `finally` block before `server_close()`. Pass the service through the HTTP server instance so handlers do not use mutable module globals.

```python
server = EvalHubHTTPServer((host, port), EvalHubRequestHandler, scheduler_service=service)
service.start()
try:
    server.serve_forever()
finally:
    service.shutdown()
    server.server_close()
```

- [ ] **Step 5: Run API and full backend tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_api.py tests/test_scheduler_service.py -q`

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 6: Commit scheduler APIs**

```bash
git add src/evalhub/server.py src/evalhub/cli.py tests/test_scheduler_api.py
git commit -m "feat: expose persistent evaluation schedules"
```

### Task 6: Chinese Scheduling Center UI

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/app.js`
- Create: `tests/test_scheduler_frontend_contract.py`

**Interfaces:**
- Consumes: scheduler health, schedules, runs, suites, and model options APIs.
- Produces: scheduling-center navigation, metrics, schedule table, structured trigger form, pause/resume controls, and run history.

- [ ] **Step 0: Invoke the frontend design skill**

Read and apply `frontend-design` before editing this view. Match the capability workbench's operational density, blue/white palette, Chinese-first copy, stable table dimensions, and 8px maximum card radius.

- [ ] **Step 1: Write failing static contract tests**

```python
def test_scheduler_center_mounts_exist() -> None:
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    for mount in ("schedulerView", "scheduleTable", "scheduleForm", "scheduleRuns"):
        assert f'id="{mount}"' in html


def test_cron_form_is_structured_not_a_shell_input() -> None:
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'name="cron_minute"' in html
    assert 'name="cron_hour"' in html
    assert 'name="command"' not in html
```

- [ ] **Step 2: Run and verify missing scheduler UI**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_frontend_contract.py -q`

Expected: FAIL because `schedulerView` is absent.

- [ ] **Step 3: Add view navigation and scheduler metrics**

Make “调度中心” a functional navigation control that toggles the workbench and scheduler views without a page reload. Add status, queue length, running jobs, active schedules, and recent failures as compact metrics.

```javascript
function showView(viewName) {
  document.querySelector("#workbenchView").hidden = viewName !== "workbench";
  document.querySelector("#schedulerView").hidden = viewName !== "scheduler";
  document.querySelectorAll("[data-view]").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === viewName);
  });
}
```

- [ ] **Step 4: Build the dense schedule table and actions**

Columns: name, model, suite/Benchmark, trigger rule, state, next run, last result, and icon actions for pause/resume, run now, edit, and delete. Use native confirmation for soft delete and refresh list/history after every mutation.

```javascript
async function mutateSchedule(id, action) {
  await fetchJson(`/api/schedules/${encodeURIComponent(id)}/${action}`, { method: "POST" });
  await Promise.all([refreshSchedules(), refreshScheduleRuns(), refreshSchedulerHealth()]);
}
```

- [ ] **Step 5: Build the structured schedule form**

Use trigger tabs for single time, interval, and Cron. Cron uses minute, hour, day-of-month, month, and weekday controls and shows the generated five-field expression plus `Asia/Shanghai` explanation. Default `sample_mode` is `all`; include timeout, retries, retry delay, coalesce, and grace-period fields.

```javascript
function cronExpression(formData) {
  return ["cron_minute", "cron_hour", "cron_day", "cron_month", "cron_weekday"]
    .map((name) => String(formData.get(name) || "*"))
    .join(" ");
}
```

- [ ] **Step 6: Add run history and report links**

Show scheduled time, actual trigger time, attempt, status, Evaluation Job ID, duration, and error. Link successful/partial rows to the capability report and keep failed messages escaped through `textContent`.

```javascript
function appendRunCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value == null ? "-" : String(value);
  row.appendChild(cell);
}
```

- [ ] **Step 7: Run syntax and frontend contract tests**

Run: `node --check frontend/app.js`

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_frontend_contract.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the scheduling center**

```bash
git add frontend tests/test_scheduler_frontend_contract.py
git commit -m "feat: add Chinese evaluation scheduling center"
```

### Task 7: Documentation, Restart Test, and Browser Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/LOCAL_RUN.md`
- Modify: `docs/API.md`
- Modify: `docs/DATA_MODEL.md`
- Modify: `docs/ARCHITECTURE.md`
- Test: `tests/test_scheduler_restart.py`

**Interfaces:**
- Consumes: completed local scheduler implementation.
- Produces: documented operations and verified restart persistence.

- [ ] **Step 1: Write the restart integration test**

Start a scheduler with a temporary SQLite file, create an active future schedule, shut down, recreate the repository/service, and assert the same schedule ID and UTC `next_run_at` are registered. Trigger an Oracle evaluation and verify ScheduleRun links to its report.

- [ ] **Step 2: Run the restart test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_restart.py -q`

Expected: PASS.

- [ ] **Step 3: Document installation and operations**

Document:

```bash
.venv/bin/python -m pip install -e ".[dev,scheduler]"
./scripts/start_local.sh
```

Explain `.runtime/evalhub.db`, backup behavior, timezone semantics, misfire/coalesce, retries, pause/delete behavior, one-worker limits, and the future distributed adapter boundary.

- [ ] **Step 4: Run complete automated verification**

Run: `PYTHONPYCACHEPREFIX=/tmp/evalhub-pyc PYTHONPATH=src .venv/bin/python -m pytest -q`

Run: `PYTHONPYCACHEPREFIX=/tmp/evalhub-pyc .venv/bin/python -m compileall -q src run_evalhub.py tests`

Run: `node --check frontend/app.js`

Expected: all commands exit 0.

- [ ] **Step 5: Verify local startup and restart**

Run `./scripts/start_local.sh`, create a future schedule in the UI, stop the service cleanly, start it again, and confirm `/api/schedules` retains the plan and `/api/scheduler/health` reports it registered.

- [ ] **Step 6: Verify desktop and mobile scheduling views**

Invoke `browser:control-in-app-browser` and capture 1440x900 and 390x844 screenshots. Confirm table controls do not overlap, the form fits without horizontal page overflow, Chinese labels remain readable, and status/actions remain stable while data refreshes.

- [ ] **Step 7: Commit documentation and restart coverage**

```bash
git add README.md docs/LOCAL_RUN.md docs/API.md docs/DATA_MODEL.md docs/ARCHITECTURE.md tests/test_scheduler_restart.py
git commit -m "docs: document local evaluation scheduling"
```
