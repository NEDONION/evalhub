# Ollama 模型与 Benchmark 资产管理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 EvalHub UI 中提供由用户显式触发、可展示大小/进度/速度/ETA 且可取消的 Ollama 模型下载，并让已缓存 Benchmark 的“更新”真正安全刷新本地资产。

**Architecture:** 后端新增独立 `OllamaPullManager`，通过 Ollama `/api/pull` NDJSON 流维护带锁的内存任务状态，React 端用短轮询展示进度。模型大小从 `/api/tags` 实际值或推荐目录预估值获得；数据集强制更新使用临时下载、校验和安全替换，失败时保留旧缓存。

**Tech Stack:** Python 3.11+ 标准库、`ThreadingHTTPServer`、React 19、TypeScript 7、Vitest、pytest、Ruff、Vite。

## Global Constraints

- 不自动下载模型；只有用户点击“下载模型”才创建 Pull 任务。
- 模型下载接口只允许 `http://127.0.0.1`、`http://localhost` 或 `http://[::1]` Ollama 地址。
- 不引入 WebSocket、SSE、数据库或新的 Python/前端第三方依赖。
- 同一时间只执行一个模型下载任务；相同模型重复创建必须返回现有任务。
- 下载前耗时区间明确标注按 20–100 Mbps 估算，下载中 ETA 使用真实字节进度计算。
- Benchmark 强制更新失败时必须保留原有可用缓存。
- `prepare_dataset()` 默认继续幂等；只有显式 `force=True` 才更新已有缓存。
- 所有网络边界在自动化测试中使用 Fake，不访问公网或真实 Ollama。
- 保留工作区中与本计划无关的并发修改，提交时只暂存每个任务列出的文件。
- 新设计和计划文档继续使用 `YYYYMMDD_中文标题.md` 命名。

---

## File Map

- Create `src/evalhub/ollama_pull.py`: 模型 Pull 任务、输入校验、NDJSON 解析、进度计算和线程生命周期。
- Modify `src/evalhub/ollama.py`: 推荐模型预估大小与 `/api/tags` 实际大小归一化。
- Modify `src/evalhub/server.py`: 模型 Pull 路由、数据集 `force` 参数与响应增强。
- Modify `src/evalhub/datasets/loaders.py`: GSM8K/MMLU 临时下载、校验与安全替换。
- Create `tests/test_ollama_pull.py`: Pull Manager 纯本地契约测试。
- Create `tests/test_dataset_refresh.py`: 数据集强制更新与旧缓存恢复测试。
- Modify `tests/test_ollama_status.py`: 模型大小来源测试。
- Modify `tests/test_server_frontend.py`: 新 HTTP 路由与数据集响应测试。
- Modify `frontend/src/types.ts`: 模型大小、Pull Task、数据集更新响应类型。
- Modify `frontend/src/lib/api.ts`: Pull 创建/查询/取消与 `force` 数据集请求。
- Modify `frontend/src/lib/api.test.ts`: 新请求契约测试。
- Create `frontend/src/lib/assets.ts`: 字节、速度、时长和下载前区间格式化。
- Create `frontend/src/lib/assets.test.ts`: 格式化边界测试。
- Modify `frontend/src/hooks/useEvalHub.ts`: Pull 任务轮询、成功刷新和数据集通知。
- Modify `frontend/src/components/dashboard/OllamaPanel.tsx`: 模型资产操作与进度 UI。
- Modify `frontend/src/components/dashboard/EvaluationForm.tsx`: 未安装模型评测拦截与数据集强制更新参数。
- Modify `frontend/src/components/dashboard/DatasetTable.tsx`: 更新状态和成功消息。
- Modify `frontend/src/App.tsx`: 新状态和回调装配。
- Modify `frontend/src/App.test.tsx`: 完整用户交互测试。
- Modify `docs/getting-started/20260804_Ollama本地模型安装与验证.md`: UI 下载流程说明。
- Modify `docs/getting-started/20260804_本地运行指南.md`: 模型与数据集资产操作说明。

---

### Task 1: Expose actual and estimated model sizes

**Files:**
- Modify: `src/evalhub/ollama.py`
- Modify: `tests/test_ollama_status.py`

**Interfaces:**
- Consumes: Ollama `/api/tags` items with `name|model` and optional `size`.
- Produces: `model_options[*].size_bytes: int | None` and `size_kind: "actual" | "estimated" | "unknown"`.

- [ ] **Step 1: Write failing status tests**

Add assertions that an installed model uses the API size and an uninstalled recommendation uses its catalog estimate:

```python
def test_installed_model_size_overrides_catalog_estimate(self) -> None:
    response = _Response(
        b'{"models":[{"name":"qwen2.5:1.5b","size":987654321}]}'
    )
    with (
        patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
        patch("evalhub.ollama.urlopen", return_value=response),
    ):
        status = get_ollama_status(model="qwen2.5:1.5b")

    option = next(item for item in status["model_options"] if item["name"] == "qwen2.5:1.5b")
    self.assertEqual(option["size_bytes"], 987654321)
    self.assertEqual(option["size_kind"], "actual")


def test_uninstalled_recommended_model_exposes_estimated_size(self) -> None:
    response = _Response(b'{"models":[]}')
    with (
        patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
        patch("evalhub.ollama.urlopen", return_value=response),
    ):
        status = get_ollama_status(model="qwen2.5:1.5b")

    option = next(item for item in status["model_options"] if item["name"] == "qwen2.5:1.5b")
    self.assertEqual(option["size_bytes"], 986_000_000)
    self.assertEqual(option["size_kind"], "estimated")
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_ollama_status.py -q`

Expected: FAIL because size fields are absent.

- [ ] **Step 3: Add estimates and preserve API model metadata**

Add `estimated_size_bytes` to every recommended entry and pass a `dict[str, int]` of actual sizes into `_build_model_options`:

```python
RECOMMENDED_OLLAMA_MODELS = [
    {
        "name": "qwen2.5:0.5b",
        "label": "Qwen2.5 0.5B",
        "description": "默认轻量模型，适合快速验证中文和数学任务。",
        "estimated_size_bytes": 397_000_000,
    },
    {
        "name": "qwen2.5:1.5b",
        "label": "Qwen2.5 1.5B",
        "description": "轻量中文能力更好，适合本地评测入门。",
        "estimated_size_bytes": 986_000_000,
    },
]
```

Keep the remaining recommended models and add their explicitly approximate byte values. Build the installed size map from `/api/tags`:

```python
model_items = body.get("models", [])
models = [str(item.get("name") or item.get("model")) for item in model_items]
actual_sizes = {
    str(item.get("name") or item.get("model")): int(item["size"])
    for item in model_items
    if (item.get("name") or item.get("model")) and isinstance(item.get("size"), int)
}
```

Each option uses actual size first, otherwise estimate, otherwise `None` and `unknown`.

- [ ] **Step 4: Run focused tests and Ruff**

Run: `.venv/bin/python -m pytest tests/test_ollama_status.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/ollama.py tests/test_ollama_status.py`

Expected: all pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/evalhub/ollama.py tests/test_ollama_status.py
git commit -m "feat: expose Ollama model sizes"
```

---

### Task 2: Implement the Ollama Pull manager

**Files:**
- Create: `src/evalhub/ollama_pull.py`
- Create: `tests/test_ollama_pull.py`

**Interfaces:**
- Consumes: `POST {base_url}/api/pull` NDJSON events.
- Produces: `OllamaPullManager.start(model: str, base_url: str) -> dict[str, object]`.
- Produces: `OllamaPullManager.get(model: str) -> dict[str, object] | None`.
- Produces: `OllamaPullManager.cancel(model: str) -> dict[str, object] | None`.

- [ ] **Step 1: Write validation and progress tests**

Create a Fake response yielding Ollama events and tests for loopback validation, idempotency and terminal progress:

```python
EVENTS = [
    {"status": "pulling manifest"},
    {"status": "pulling layer", "completed": 50, "total": 100},
    {"status": "pulling layer", "completed": 100, "total": 100},
    {"status": "verifying sha256 digest"},
    {"status": "success"},
]


def test_rejects_non_loopback_ollama_url() -> None:
    manager = OllamaPullManager(opener=lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="loopback"):
        manager.start("qwen2.5:1.5b", "http://example.com:11434")


def test_tracks_pull_progress_to_success() -> None:
    response = FakeResponse(EVENTS)
    manager = OllamaPullManager(opener=lambda *_args, **_kwargs: response)
    first = manager.start("qwen2.5:1.5b", "http://127.0.0.1:11434")
    second = manager.start("qwen2.5:1.5b", "http://127.0.0.1:11434")
    assert second["model"] == first["model"]
    task = wait_for_terminal(manager, "qwen2.5:1.5b")
    assert task["status"] == "success"
    assert task["completed_bytes"] == 100
    assert task["total_bytes"] == 100
```

Also cover Ollama `{ "error": "..." }`, cancel, invalid model names and only one active worker.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_ollama_pull.py -q`

Expected: collection fails because `evalhub.ollama_pull` does not exist.

- [ ] **Step 3: Implement task state and validation**

Use a dataclass whose public serialization excludes internal synchronization fields:

```python
@dataclass
class OllamaPullTask:
    model: str
    base_url: str
    status: str = "pending"
    message: str = "等待下载"
    completed_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    response: object | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "status": self.status,
            "message": self.message,
            "completed_bytes": self.completed_bytes,
            "total_bytes": self.total_bytes,
            "speed_bytes_per_second": self.speed_bytes_per_second,
            "eta_seconds": self.eta_seconds,
            "error": self.error,
        }
```

Validate model names with a compiled allow-list regex and URLs with `urllib.parse.urlparse`; reject credentials, non-HTTP schemes and non-loopback hostnames.

- [ ] **Step 4: Implement the threaded NDJSON worker**

`start()` stores the task under a lock and starts a daemon thread. The worker acquires a global execution lock, sends JSON with `stream: true`, reads one JSON object per line, and updates a task copy under the state lock:

```python
request = Request(
    f"{base_url.rstrip('/')}/api/pull",
    data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with self._opener(request, timeout=30) as response:
    for raw_line in response:
        if task.cancel_event.is_set():
            self._finish_canceled(task)
            return
        event = json.loads(raw_line.decode("utf-8"))
        self._apply_event(task, event)
```

Calculate speed from positive byte/time deltas using `time.monotonic`; calculate ETA only when speed is positive and total exceeds completed. Map messages containing `verifying` to `verifying`, `success` to `success`, and exceptions to `failed` unless canceled.

- [ ] **Step 5: Run Pull tests and Ruff**

Run: `.venv/bin/python -m pytest tests/test_ollama_pull.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/ollama_pull.py tests/test_ollama_pull.py`

Expected: all pass without real network access.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/evalhub/ollama_pull.py tests/test_ollama_pull.py
git commit -m "feat: manage Ollama model downloads"
```

---

### Task 3: Expose model Pull HTTP routes

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_server_frontend.py`

**Interfaces:**
- Consumes: Task 2 `OllamaPullManager` methods.
- Produces: `POST /api/ollama/pulls`, `GET /api/ollama/pulls?model=...`, `DELETE /api/ollama/pulls?model=...`.

- [ ] **Step 1: Add failing handler tests**

Extend server tests with a local handler factory and a patched manager. Assert:

```python
response = request_json(
    server,
    "POST",
    "/api/ollama/pulls",
    {"model": "qwen2.5:1.5b", "base_url": "http://127.0.0.1:11434"},
)
self.assertEqual(response.status, 202)
self.assertEqual(response.json()["task"]["model"], "qwen2.5:1.5b")
```

Add GET-null, GET-active, DELETE-active, invalid payload HTTP 400 and unknown path coverage.

- [ ] **Step 2: Run the server tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_server_frontend.py -q`

Expected: new routes return 404 or handler lacks `do_DELETE`.

- [ ] **Step 3: Add the routes with one module-level manager**

In `server.py`:

```python
OLLAMA_PULL_MANAGER = OllamaPullManager()
```

- GET returns `{"ok": True, "task": task_or_none}`.
- POST validates JSON fields, calls `start`, and returns 202.
- DELETE requires a model query, calls `cancel`, and returns 200; missing tasks return 404.
- `ValueError` returns HTTP 400, unexpected errors return HTTP 500.

- [ ] **Step 4: Run focused server tests and Ruff**

Run: `.venv/bin/python -m pytest tests/test_server_frontend.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/server.py tests/test_server_frontend.py`

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/evalhub/server.py tests/test_server_frontend.py
git commit -m "feat: expose Ollama download API"
```

---

### Task 4: Safely refresh cached Benchmark datasets

**Files:**
- Modify: `src/evalhub/datasets/loaders.py`
- Create: `tests/test_dataset_refresh.py`
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_server_frontend.py`

**Interfaces:**
- Produces: `prepare_dataset(name: str, *, root: Path | str = ".", force: bool = False) -> Path`.
- Produces: `/api/datasets/prepare` response fields `operation` and `sample_count`.

- [ ] **Step 1: Write failing GSM8K refresh tests**

Create an existing valid cache, patch `urlretrieve` to write different valid JSONL, and assert `force=False` preserves while `force=True` replaces:

```python
def test_force_refresh_replaces_gsm8k_only_after_validation(tmp_path: Path) -> None:
    target = tmp_path / "data/raw/gsm8k/test.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text(valid_gsm8k("old", "1"), encoding="utf-8")

    with patch("evalhub.datasets.loaders.urlretrieve", side_effect=write_new_gsm8k):
        prepare_dataset("gsm8k", root=tmp_path, force=True)

    assert "new" in target.read_text(encoding="utf-8")
```

Add a validation-failure test asserting the exact old bytes remain and no `.part` file survives.

- [ ] **Step 2: Write failing MMLU refresh tests**

Build a tiny tar archive containing `data/test/abstract_algebra_test.csv`, patch `urlretrieve`, and assert force refresh swaps the test data. Add unsafe member and incomplete archive cases that preserve the old directory.

- [ ] **Step 3: Run dataset tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_dataset_refresh.py -q`

Expected: FAIL because `force` is not accepted and existing caches short-circuit.

- [ ] **Step 4: Implement atomic GSM8K refresh**

Update the public signature and dispatch. On force, download to a `NamedTemporaryFile` in the target directory, parse every non-empty line as JSON, require `question` and an extractable official answer, require at least one row, then call `candidate.replace(local_path)`. Always unlink the candidate in `finally` if it still exists.

- [ ] **Step 5: Implement validated MMLU directory swap**

Use `TemporaryDirectory(dir=mmlu_root)` to download and safely extract a candidate archive. Require `candidate/data/test/abstract_algebra_test.csv` and at least one `*_test.csv`. Rename current `data` to a unique backup, rename candidate `data` into place, replace `data.tar`, then remove backup. If any swap step fails, rename the backup back before propagating the error.

- [ ] **Step 6: Add API `force`, operation and sample count**

Before prepare, compute whether the requested cache exists. Require `force` to be a JSON boolean. Call:

```python
path = prepare_dataset(dataset, force=force)
samples = load_samples(
    dataset,
    limit=100000,
    subject="abstract_algebra" if dataset == "mmlu" else None,
)
operation = "updated" if force and was_prepared else "cached"
```

Return `operation` and `len(samples)`.

- [ ] **Step 7: Run dataset/server tests and Ruff**

Run: `.venv/bin/python -m pytest tests/test_dataset_refresh.py tests/test_server_frontend.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/datasets/loaders.py src/evalhub/server.py tests/test_dataset_refresh.py tests/test_server_frontend.py`

Expected: all pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add src/evalhub/datasets/loaders.py src/evalhub/server.py tests/test_dataset_refresh.py tests/test_server_frontend.py
git commit -m "fix: refresh cached benchmark assets safely"
```

---

### Task 5: Add frontend asset types, API clients and formatters

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Create: `frontend/src/lib/assets.ts`
- Create: `frontend/src/lib/assets.test.ts`

**Interfaces:**
- Produces: `ModelOption.size_bytes`, `ModelOption.size_kind`.
- Produces: `OllamaPullTask`, `OllamaPullResponse`.
- Produces: `startModelPull`, `getModelPull`, `cancelModelPull`, `prepareDataset(dataset, force)`.
- Produces: `formatBytes`, `formatRate`, `formatDuration`, `estimateDownloadRange`.

- [ ] **Step 1: Write failing API request tests**

Assert exact model encoding, HTTP methods and data payloads:

```typescript
await startModelPull("qwen2.5:1.5b", "http://127.0.0.1:11434");
expect(fetchMock).toHaveBeenCalledWith(
  "/api/ollama/pulls",
  expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ model: "qwen2.5:1.5b", base_url: "http://127.0.0.1:11434" }),
  }),
);

await getModelPull("qwen2.5:1.5b");
expect(fetchMock).toHaveBeenCalledWith(
  "/api/ollama/pulls?model=qwen2.5%3A1.5b",
  expect.any(Object),
);

await prepareDataset("gsm8k", true);
expect(fetchMock).toHaveBeenCalledWith(
  "/api/datasets/prepare",
  expect.objectContaining({ body: JSON.stringify({ dataset: "gsm8k", force: true }) }),
);
```

- [ ] **Step 2: Write failing formatter tests**

```typescript
expect(formatBytes(986_000_000)).toBe("986 MB");
expect(formatRate(31_400_000)).toBe("31.4 MB/s");
expect(formatDuration(44)).toBe("44 秒");
expect(estimateDownloadRange(986_000_000)).toEqual({ minimumSeconds: 79, maximumSeconds: 395 });
```

- [ ] **Step 3: Run frontend tests and confirm RED**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts src/lib/assets.test.ts`

Expected: FAIL because new exports and file do not exist.

- [ ] **Step 4: Add types and API functions**

Define:

```typescript
export type ModelSizeKind = "actual" | "estimated" | "unknown";
export type PullStatus = "pending" | "pulling" | "verifying" | "success" | "failed" | "canceled";

export interface OllamaPullTask {
  model: string;
  status: PullStatus;
  message: string;
  completed_bytes: number | null;
  total_bytes: number | null;
  speed_bytes_per_second: number | null;
  eta_seconds: number | null;
  error: string | null;
}
```

Implement API functions through existing `fetchJson`; use `URLSearchParams` for GET/DELETE queries.

- [ ] **Step 5: Implement deterministic formatters**

Use decimal asset units (1 MB = 1,000,000 bytes) to match Ollama sizes. Return `—` for null values and round durations up. Keep `estimateDownloadRange` pure and use 100 Mbps/20 Mbps formulas from the spec.

- [ ] **Step 6: Run API/formatter tests, typecheck and commit**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts src/lib/assets.test.ts`

Run: `npm --prefix frontend run typecheck`

```bash
git add frontend/src/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/lib/assets.ts frontend/src/lib/assets.test.ts
git commit -m "feat: add local asset API clients"
```

---

### Task 6: Orchestrate model Pull polling and dataset notices

**Files:**
- Modify: `frontend/src/hooks/useEvalHub.ts`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 5 API clients and `OllamaPullTask`.
- Produces: hook fields `modelPullTask`, `modelPullError`, `datasetNotice`, `startModelPull`, `cancelModelPull`, `prepare(dataset, force)`.

- [ ] **Step 1: Add failing hook-through-App tests**

Mock a pending task followed by a successful task using fake timers. Assert `startModelPull` is called once, `getModelPull` is polled, and `getOllamaStatus` refreshes after success. Remount the page with an active task and assert the hook recovers it by querying the selected model. Add cancel and dataset-success notice tests.

```typescript
vi.mocked(startModelPull).mockResolvedValue({ ok: true, task: pullingTask });
vi.mocked(getModelPull).mockResolvedValueOnce({ ok: true, task: pullingTask }).mockResolvedValue({ ok: true, task: successTask });
```

- [ ] **Step 2: Run the focused App tests and confirm RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL because the hook does not expose Pull state or dataset notices.

- [ ] **Step 3: Implement Pull state and polling**

`startModelPull` clears old errors and stores the returned task. A model-change `useEffect` calls `getModelPull(model)` once so a page refresh can recover an active task. A second `useEffect` schedules one 500 ms `setTimeout` only while status is `pending|pulling|verifying`. Each poll replaces the task. On success call `refresh()` once; on failed copy `task.error` into `modelPullError`. Cleanup clears the timer and ignores late promise results.

- [ ] **Step 4: Implement cancellation and dataset notices**

`cancelModelPull` calls DELETE and stores the returned task. Change prepare signature to `(dataset, force)` and use response fields:

```typescript
const response = await prepareDataset(dataset, force);
setDatasetNotice(
  `${dataset.toUpperCase()} 已${response.operation === "updated" ? "更新" : "缓存"}，${response.sample_count.toLocaleString("zh-CN")} 条样本`,
);
```

Clear the notice at the beginning of the next dataset operation.

- [ ] **Step 5: Run App tests and commit**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Run: `npm --prefix frontend run typecheck`

```bash
git add frontend/src/hooks/useEvalHub.ts frontend/src/App.test.tsx
git commit -m "feat: track local asset operations"
```

---

### Task 7: Build the model download UI and prevent missing-model evaluations

**Files:**
- Modify: `frontend/src/components/dashboard/OllamaPanel.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 5 formatters and Task 6 hook fields.
- Produces: explicit “下载模型” / “暂不下载”, progress/ETA/cancel UI, and client-side evaluation guard.

- [ ] **Step 1: Add failing UI tests for the opt-in choice**

Render an uninstalled model and assert estimated metadata and choice buttons:

```typescript
expect(await screen.findByText("约 986 MB")).toBeInTheDocument();
expect(screen.getByText(/按 20–100 Mbps/)).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "下载 qwen2.5:1.5b" }));
expect(startModelPull).toHaveBeenCalledWith("qwen2.5:1.5b", "http://127.0.0.1:11434");
```

Click “暂不下载” and assert no Pull request. Test progress values, cancel, failure/retry and success.

- [ ] **Step 2: Add a failing evaluation guard test**

Select an uninstalled option, click evaluation, and assert:

```typescript
expect(screen.getByText("先下载模型或选择已安装模型")).toBeInTheDocument();
expect(runEvaluation).not.toHaveBeenCalled();
```

- [ ] **Step 3: Run App tests and confirm RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: missing UI and guard assertions fail.

- [ ] **Step 4: Extend `OllamaPanel` with asset actions**

Pass the selected `ModelOption`, Pull task, errors and callbacks. Show actual/estimated size labels, the transparent pre-download range, a progress element with `aria-valuenow`, and formatted completed/total/speed/ETA. Only show download actions when Ollama is installed, running and the selected model is absent.

- [ ] **Step 5: Wire decline behavior in `App`**

On “暂不下载”, select the first installed option. If none exists, set a local dismissed model name so the prompt collapses while readiness remains “缺少模型”. Clear dismissal when model changes.

- [ ] **Step 6: Guard the evaluation form**

Resolve the selected option from `modelOptions`. If adapter is Ollama and it is not installed, show a model field error and disable submit. Keep Oracle evaluation enabled. Include size in every option label:

```tsx
{option.label} · {formatBytes(option.size_bytes)} · {option.installed ? "已安装" : "未下载"}
```

- [ ] **Step 7: Run App tests, accessibility assertions and typecheck**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Run: `npm --prefix frontend run typecheck`

Expected: all pass.

- [ ] **Step 8: Commit Task 7**

```bash
git add frontend/src/components/dashboard/OllamaPanel.tsx frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat: add model download controls"
```

---

### Task 8: Make Benchmark update observable and forceful

**Files:**
- Modify: `frontend/src/components/dashboard/DatasetTable.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 6 `prepare(dataset, force)` and `datasetNotice`.
- Produces: correct `force` behavior and visible update feedback in both dataset entry points.

- [ ] **Step 1: Add failing cached-update tests**

Click the GSM8K table action and assert `prepareDataset("gsm8k", true)`. Click uncached MMLU and assert `false`. Resolve the request and assert the success message with sample count. Reject it and assert old “已缓存” state remains alongside the error.

- [ ] **Step 2: Run App tests and confirm RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: current handler has no `force` argument and no success notice.

- [ ] **Step 3: Pass force from both dataset actions**

Change callback signatures to `(dataset: DatasetName, force: boolean)`. Table buttons pass `dataset.prepared`; the Evaluation Form finds the selected Dataset and passes its `prepared` value.

- [ ] **Step 4: Render operation-specific state and notice**

Use the selected dataset’s existing state to render “缓存中” or “更新中”. Show `datasetNotice` in a `role="status"` success strip and retain `datasetError` in `role="alert"`.

- [ ] **Step 5: Run App tests, typecheck and commit**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Run: `npm --prefix frontend run typecheck`

```bash
git add frontend/src/components/dashboard/DatasetTable.tsx frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "fix: make benchmark updates observable"
```

---

### Task 9: Document and verify the complete asset workflow

**Files:**
- Modify: `docs/getting-started/20260804_Ollama本地模型安装与验证.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Verify: all implementation and test files from Tasks 1–8.

**Interfaces:**
- Consumes: completed HTTP and UI behavior.
- Produces: verified, documented and push-ready repository state.

- [ ] **Step 1: Update user documentation**

Document that selecting a model never downloads it automatically, explain estimated versus actual size, progress/ETA/cancel behavior, missing-model evaluation blocking, and the Benchmark “更新” safety guarantee. Preserve CLI `ollama pull` as an alternative.

- [ ] **Step 2: Run complete Python verification**

Run: `.venv/bin/python -m ruff check .`

Run: `.venv/bin/python -m pytest`

Expected: all checks and tests pass.

- [ ] **Step 3: Run complete frontend verification**

Run: `npm --prefix frontend run test:run`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: all tests pass and `frontend/dist` builds successfully.

- [ ] **Step 4: Run repository hygiene checks**

Run: `git diff --check`

Run: `rg -n 'TODO|TBD|PLACEHOLDER' src frontend/src tests docs/getting-started`

Expected: no whitespace errors and no new placeholders.

- [ ] **Step 5: Perform local API smoke checks**

With EvalHub and Ollama running, verify `/api/ollama/status` reports the installed `qwen2.5:1.5b` actual size. Use a Fake-backed automated test for a missing model rather than downloading another large model. Verify a cached dataset update changes its file modification time only after a successful response.

- [ ] **Step 6: Commit docs and final integration fixes**

```bash
git add docs/getting-started/20260804_Ollama本地模型安装与验证.md docs/getting-started/20260804_本地运行指南.md
git commit -m "docs: explain local asset downloads"
```

- [ ] **Step 7: Review scope and push**

Inspect `git status -sb`, `git log origin/main..main`, and every commit diff. Do not stage unrelated pre-existing files. Push the current `main` only after all validation is green:

```bash
git push origin main
```
