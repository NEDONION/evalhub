# Agent Benchmark Difficulty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend EvalHub Coding Mini to six auditable tasks split evenly across easy, medium, and hard, with selectable execution and per-tier reporting.

**Architecture:** Reuse the existing `TaskRequest → SubprocessEvaluationExecutor → Coding Mini → persisted result → React` path. Add one request value, two fields on the existing sample dataclass, filtering and aggregation in `coding_mini.py`, and small form/result additions; keep SQLite schemas, workers, trace storage, and Codex Runner unchanged.

**Tech Stack:** Python 3.11+, dataclasses, standard library, SQLite JSON persistence, pytest, React, TypeScript, Vitest, Testing Library.

## Global Constraints

- Use the existing worktree and branch `codex/agent-live-trace`; do not modify the dirty main checkout.
- Add no Python, JavaScript, database, or service dependencies.
- Keep hidden Verifier source out of Agent workspaces, prompts, and pre-completion trace events.
- Keep existing public model-evaluation behavior unchanged.
- New or modified Python functions require detailed Chinese docstrings and informative Chinese comments at logical boundaries.
- New or modified frontend functions require detailed Chinese JSDoc.
- Use TDD: write each behavioral test, observe the expected failure, implement the minimum, then rerun the focused tests.
- Do not add a Benchmark registry, database table, uploader, dynamic calibration, or difficulty-weighted score.

---

### Task 1: Six-task difficulty catalog, selection, trace, and aggregation

**Files:**
- Modify: `src/evalhub/benchmarks/coding_mini.py:12-260,324-415,605-660`
- Modify: `tests/test_coding_mini.py:1-260`

**Interfaces:**
- Consumes: existing `CodingAgentSample`, `AgentRunner`, `AgentTraceEvent`, workspace creation, hidden verification, capability aggregation, and failed-example formatting.
- Produces: `CodingAgentSample.difficulty`, `CodingAgentSample.difficulty_reason`, `run_codex_agent_benchmark(..., difficulty: str, ...)`, `_select_samples(difficulty: str)`, `benchmark_version`, `requested_difficulty`, and `difficulty_report`.

- [ ] **Step 1: Add failing catalog and selector tests**

Import `Counter`, `coding_mini_samples`, and `_select_samples`, then add:

```python
def test_coding_mini_catalog_has_two_explained_samples_per_difficulty() -> None:
    """内置题集应提供稳定、无重复且每档两道的三级难度样本。"""
    samples = coding_mini_samples()

    assert len(samples) == 6
    assert len({sample.id for sample in samples}) == 6
    assert Counter(sample.difficulty for sample in samples) == {
        "easy": 2,
        "medium": 2,
        "hard": 2,
    }
    assert all(sample.difficulty_reason.strip() for sample in samples)


@pytest.mark.parametrize(
    ("difficulty", "expected_ids"),
    [
        ("all", [
            "pricing_total",
            "cart_quantity",
            "slug_normalization",
            "inventory_reservation",
            "batch_reservation_atomicity",
            "retry_state_machine",
        ]),
        ("easy", ["pricing_total", "cart_quantity"]),
        ("medium", ["slug_normalization", "inventory_reservation"]),
        ("hard", ["batch_reservation_atomicity", "retry_state_machine"]),
    ],
)
def test_coding_mini_selects_stable_difficulty_groups(
    difficulty: str,
    expected_ids: list[str],
) -> None:
    """全部和单档选择都应返回固定顺序，保证报告可复现。"""
    assert [sample.id for sample in _select_samples(difficulty)] == expected_ids


def test_coding_mini_rejects_unknown_difficulty() -> None:
    """未知难度不得静默退化为全部题目。"""
    with pytest.raises(ValueError, match="difficulty must be one of"):
        _select_samples("expert")
```

- [ ] **Step 2: Run the catalog tests and confirm RED**

Run:

```bash
PYTHONPATH=src /Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m pytest \
  tests/test_coding_mini.py::test_coding_mini_catalog_has_two_explained_samples_per_difficulty \
  tests/test_coding_mini.py::test_coding_mini_selects_stable_difficulty_groups \
  tests/test_coding_mini.py::test_coding_mini_rejects_unknown_difficulty -q
```

Expected: FAIL because `CodingAgentSample` has no difficulty fields and `_select_samples` still expects a limit.

- [ ] **Step 3: Add the minimum catalog and selector implementation**

Add the two frozen dataclass fields:

```python
difficulty: Literal["easy", "medium", "hard"]
difficulty_reason: str
```

Import `Literal`. Keep the six samples in this exact order and define the three new fixtures as follows:

```python
CodingAgentSample(
    id="cart_quantity",
    difficulty="easy",
    difficulty_reason="单文件纯函数，只有局部聚合语义",
    instruction=(
        "Fix cart.total_quantity so it sums the integer quantity from every supplied "
        "line item. Preserve the public function signature, handle an empty cart, and "
        "verify the change."
    ),
    files={"cart.py": "def total_quantity(lines):\n    return len(lines)\n"},
    verifier_code=(
        "from cart import total_quantity\n"
        "assert total_quantity([{'quantity': 2}, {'quantity': 3}]) == 5\n"
        "assert total_quantity([{'quantity': 0}]) == 0\n"
        "assert total_quantity([]) == 0\n"
    ),
    capability_weights={"tool_use": 0.3, "verification": 0.4, "robustness": 0.3},
)
```

```python
CodingAgentSample(
    id="batch_reservation_atomicity",
    difficulty="hard",
    difficulty_reason="需要理解两文件调用关系和原子性",
    instruction=(
        "Repair batch.reserve_batch without changing its signature. Requests are "
        "(item, quantity) pairs and duplicate items are cumulative. Apply every "
        "reservation only when all quantities are positive and sufficient; otherwise "
        "leave the original stock unchanged. Verify success and rollback paths."
    ),
    files={
        "inventory.py": (
            "def reserve(stock, item, quantity):\n"
            "    if quantity <= 0 or stock.get(item, 0) < quantity:\n"
            "        return False\n"
            "    stock[item] -= quantity\n"
            "    return True\n"
        ),
        "batch.py": (
            "from inventory import reserve\n\n"
            "def reserve_batch(stock, requests):\n"
            "    return all(reserve(stock, item, quantity) for item, quantity in requests)\n"
        ),
    },
    verifier_code=(
        "from batch import reserve_batch\n"
        "stock = {'pen': 4, 'book': 2}\n"
        "assert reserve_batch(stock, [('pen', 2), ('pen', 1), ('book', 2)]) is True\n"
        "assert stock == {'pen': 1, 'book': 0}\n"
        "stock = {'pen': 4, 'book': 2}\n"
        "assert reserve_batch(stock, [('pen', 2), ('book', 3)]) is False\n"
        "assert stock == {'pen': 4, 'book': 2}\n"
        "assert reserve_batch(stock, [('missing', 1)]) is False and 'missing' not in stock\n"
        "assert reserve_batch(stock, [('pen', 0)]) is False and stock == {'pen': 4, 'book': 2}\n"
    ),
    capability_weights={
        "planning": 0.2,
        "code_understanding": 0.2,
        "implementation": 0.2,
        "tool_use": 0.1,
        "verification": 0.1,
        "robustness": 0.2,
    },
)
```

```python
CodingAgentSample(
    id="retry_state_machine",
    difficulty="hard",
    difficulty_reason="多文件状态定义和多步状态不变量",
    instruction=(
        "Repair retry.record_failure without changing its signature. Running or retrying "
        "jobs increment attempts and store the error. Return True and set retrying below "
        "max_attempts; return False and set failed at the limit. Succeeded and failed jobs "
        "are terminal and must remain unchanged. Verify the state transitions."
    ),
    files={
        "states.py": "TERMINAL_STATUSES = {'succeeded', 'failed'}\n",
        "retry.py": (
            "from states import TERMINAL_STATUSES\n\n"
            "def record_failure(job, max_attempts, error):\n"
            "    job['attempts'] += 1\n"
            "    job['last_error'] = error\n"
            "    job['status'] = 'retrying'\n"
            "    return True\n"
        ),
    },
    verifier_code=(
        "from retry import record_failure\n"
        "job = {'status': 'running', 'attempts': 0, 'last_error': None}\n"
        "assert record_failure(job, 2, 'timeout') is True\n"
        "assert job == {'status': 'retrying', 'attempts': 1, 'last_error': 'timeout'}\n"
        "assert record_failure(job, 2, 'again') is False\n"
        "assert job == {'status': 'failed', 'attempts': 2, 'last_error': 'again'}\n"
        "terminal = {'status': 'succeeded', 'attempts': 1, 'last_error': None}\n"
        "assert record_failure(terminal, 3, 'ignored') is False\n"
        "assert terminal == {'status': 'succeeded', 'attempts': 1, 'last_error': None}\n"
        "failed = {'status': 'failed', 'attempts': 3, 'last_error': 'original'}\n"
        "assert record_failure(failed, 3, 'ignored') is False\n"
        "assert failed == {'status': 'failed', 'attempts': 3, 'last_error': 'original'}\n"
    ),
    capability_weights={
        "planning": 0.15,
        "code_understanding": 0.2,
        "implementation": 0.2,
        "tool_use": 0.1,
        "verification": 0.15,
        "robustness": 0.2,
    },
)
```

Annotate the existing samples with these exact values:

```python
# pricing_total
difficulty="easy"
difficulty_reason="单文件纯函数，缺陷定位直接"

# slug_normalization
difficulty="medium"
difficulty_reason="单函数但包含多个输入边界"

# inventory_reservation
difficulty="medium"
difficulty_reason="涉及可变状态与失败不变量"
```

Add `planning: 0.1` to `inventory_reservation`, reduce its robustness weight to `0.3`, and keep every sample's weights totaling 1.0.

Replace `_select_samples(limit)` with:

```python
def _select_samples(difficulty: str) -> tuple[CodingAgentSample, ...]:
    """按难度返回稳定样本组，并拒绝未知选择。

    Args:
        difficulty: 全部或单个难度标识。

    Returns:
        按内置固定顺序选择的非空样本元组。

    Raises:
        ValueError: 难度不在 all、easy、medium、hard 中。
    """
    if difficulty == "all":
        return coding_mini_samples()
    if difficulty not in {"easy", "medium", "hard"}:
        raise ValueError("difficulty must be one of: all, easy, medium, hard")
    return tuple(sample for sample in coding_mini_samples() if sample.difficulty == difficulty)
```

- [ ] **Step 4: Run catalog tests and confirm GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add failing execution/report/trace tests**

Update `EditingFakeRunner._apply_fix` with exact correct implementations for the three new samples:

```python
elif sample_id == "cart_quantity":
    (workspace / "cart.py").write_text(
        "def total_quantity(lines):\n"
        "    return sum(line['quantity'] for line in lines)\n",
        encoding="utf-8",
    )
elif sample_id == "batch_reservation_atomicity":
    (workspace / "batch.py").write_text(
        "from inventory import reserve\n\n"
        "def reserve_batch(stock, requests):\n"
        "    candidate = stock.copy()\n"
        "    for item, quantity in requests:\n"
        "        if not reserve(candidate, item, quantity):\n"
        "            return False\n"
        "    stock.clear()\n"
        "    stock.update(candidate)\n"
        "    return True\n",
        encoding="utf-8",
    )
elif sample_id == "retry_state_machine":
    (workspace / "retry.py").write_text(
        "from states import TERMINAL_STATUSES\n\n"
        "def record_failure(job, max_attempts, error):\n"
        "    if job['status'] in TERMINAL_STATUSES:\n"
        "        return False\n"
        "    job['attempts'] += 1\n"
        "    job['last_error'] = error\n"
        "    if job['attempts'] >= max_attempts:\n"
        "        job['status'] = 'failed'\n"
        "        return False\n"
        "    job['status'] = 'retrying'\n"
        "    return True\n",
        encoding="utf-8",
    )
```

Change existing benchmark calls from `limit=...` to `difficulty="all"` or the required tier. Update the full-pass test to assert progress `[(0, 6), ..., (6, 6)]`, six passes, and all six capability dimensions equal 1.0. Run the partial-score test with `difficulty="easy"`; when pricing is skipped, assert one pass, failed ID `pricing_total`, and exact dimension scores `planning=0.0`, `code_understanding=0.0`, `implementation=0.0`, `tool_use=1.0`, `verification=1.0`, `robustness=1.0`. Add these result assertions:

```python
assert result["benchmark_version"] == "coding-mini-v2"
assert result["requested_difficulty"] == "all"
assert result["difficulty_report"] == [
    {"difficulty": "easy", "total": 2, "passed": 2, "pass_rate": 1.0},
    {"difficulty": "medium", "total": 2, "passed": 2, "pass_rate": 1.0},
    {"difficulty": "hard", "total": 2, "passed": 2, "pass_rate": 1.0},
]
assert [(item["sample_id"], item["difficulty"]) for item in result["sample_results"]] == [
    ("pricing_total", "easy"),
    ("cart_quantity", "easy"),
    ("slug_normalization", "medium"),
    ("inventory_reservation", "medium"),
    ("batch_reservation_atomicity", "hard"),
    ("retry_state_machine", "hard"),
]
```

Run the trace test with `difficulty="easy"` and assert:

```python
assert events[0]["payload"]["difficulty"] == "easy"
assert events[0]["payload"]["difficulty_reason"] == "单文件纯函数，缺陷定位直接"
assert events[0]["message"].startswith("[简单]")
```

For the diagnostics classification test, import `_create_workspace`, `_run_sample`, and
`coding_mini_samples`, then replace the Benchmark call inside the case loop with:

```python
sample = coding_mini_samples()[0]
workspace = _create_workspace(tmp_path / f"job_outcome_{index}", sample)
sample_result = _run_sample(
    sample=sample,
    workspace=workspace,
    model="local-test",
    base_url="http://127.0.0.1:11434",
    runner=runner,
    on_trace=None,
)
diagnostics = sample_result["diagnostics"]
```

The trace test runs `difficulty="easy"`, so its event-type assertion must contain the five-event
sequence twice: `sample_started`, `tool_started`, `workspace_changed`, `verifier_finished`,
`sample_finished` for pricing, followed by the same sequence for cart.

- [ ] **Step 6: Run execution/report tests and confirm RED**

Run:

```bash
PYTHONPATH=src /Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m pytest tests/test_coding_mini.py -q
```

Expected: FAIL because the runner still accepts `limit`, results lack difficulty fields/report, and trace messages lack difficulty.

- [ ] **Step 7: Implement execution/report/trace fields**

Change the public runner signature to `difficulty: str`, call `_select_samples(difficulty)`, and add:

```python
"benchmark_version": "coding-mini-v2",
"requested_difficulty": difficulty,
"difficulty_report": _aggregate_difficulty(selected_samples, sample_results),
```

Add `difficulty` and `difficulty_reason` to both `_run_sample` return branches and to `_failed_examples`. Include both in `_scaffold_hash`. Emit `sample_started` with:

```python
message=f"[{_difficulty_label(sample.difficulty)}] {sample.instruction}",
payload={
    "sample_id": sample.id,
    "instruction": sample.instruction,
    "difficulty": sample.difficulty,
    "difficulty_reason": sample.difficulty_reason,
},
```

Use one small label mapping and one aggregation function:

```python
def _difficulty_label(difficulty: str) -> str:
    """把内部难度标识转换为审计时间线使用的中文标签。"""
    return {"easy": "简单", "medium": "中等", "hard": "困难"}[difficulty]


def _aggregate_difficulty(
    samples: tuple[CodingAgentSample, ...],
    sample_results: list[dict[str, object]],
) -> list[dict[str, object]]:
    """按实际选择的难度顺序汇总隐藏校验通过率。"""
    passed_ids = {
        str(result["sample_id"])
        for result in sample_results
        if result["status"] == "success"
    }
    report: list[dict[str, object]] = []
    for difficulty in ("easy", "medium", "hard"):
        tier = [sample for sample in samples if sample.difficulty == difficulty]
        if not tier:
            continue
        passed = sum(sample.id in passed_ids for sample in tier)
        report.append(
            {
                "difficulty": difficulty,
                "total": len(tier),
                "passed": passed,
                "pass_rate": round(passed / len(tier), 4),
            }
        )
    return report
```

- [ ] **Step 8: Run all Coding Mini tests and confirm GREEN**

Run the Step 6 command. Expected: all tests PASS.

- [ ] **Step 9: Commit the backend Benchmark change**

```bash
git add src/evalhub/benchmarks/coding_mini.py tests/test_coding_mini.py
git commit -m "feat: add coding mini difficulty tiers"
```

---

### Task 2: API validation, request persistence, and worker propagation

**Files:**
- Modify: `src/evalhub/tasks/models.py:10-31`
- Modify: `src/evalhub/server.py:585-662`
- Modify: `src/evalhub/tasks/executor.py:80-111`
- Modify: `tests/test_task_api.py:267-320`
- Modify: `tests/test_task_executor.py:71-116`
- Modify: `tests/test_task_repository.py:213-232`
- Modify: `docs/architecture/20260804_API接口草案.md`

**Interfaces:**
- Consumes: Task 1 `run_codex_agent_benchmark(..., difficulty: str, ...)`.
- Produces: persisted `TaskRequest.agent_difficulty: str | None`, validated API input, and worker forwarding of `all|easy|medium|hard`.

- [ ] **Step 1: Add failing API and persistence tests**

Update the accepted Agent payload to `sample_mode="all"` and `agent_difficulty="hard"`; assert:

```python
assert service.submitted_request.agent_difficulty == "hard"
assert service.submitted_request.sample_mode == "all"
```

Extend the invalid-case table with:

```python
({**base_payload, "agent_difficulty": "expert"},
 "agent_difficulty must be one of: all, easy, medium, hard"),
({**base_payload, "agent_difficulty": ""},
 "agent_difficulty must be one of: all, easy, medium, hard"),
({**base_payload, "agent_difficulty": None},
 "agent_difficulty must be one of: all, easy, medium, hard"),
```

Add a separate model request test:

```python
def test_create_model_evaluation_rejects_agent_difficulty() -> None:
    """模型评测携带 Agent 专属难度时应返回明确客户端错误。"""
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=FakeTaskService(task_fixture()),
        payload={
            "dataset": "gsm8k",
            "adapter": "oracle",
            "model": "local-test",
            "sample_mode": "all",
            "agent_difficulty": "easy",
        },
    )

    assert status == 400
    assert response == {"ok": False, "error": "agent_difficulty is only valid for agent evaluations"}
```

Set `agent_difficulty="medium"` in the repository round-trip request and assert it is restored unchanged.

- [ ] **Step 2: Run focused API/repository tests and confirm RED**

```bash
PYTHONPATH=src /Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m pytest \
  tests/test_task_api.py::test_create_agent_evaluation_returns_accepted_task \
  tests/test_task_api.py::test_create_agent_evaluation_rejects_unsupported_combinations \
  tests/test_task_api.py::test_create_model_evaluation_rejects_agent_difficulty \
  tests/test_task_repository.py::test_repository_round_trips_agent_request_fields -q
```

Expected: FAIL because `TaskRequest` and request parsing do not contain `agent_difficulty`.

- [ ] **Step 3: Add the request field and boundary validation**

Add the final dataclass field:

```python
agent_difficulty: str | None = None
```

In `_parse_evaluation_request`, reject the field on model requests, normalize missing Agent values to `all`, validate the four values, and require Agent `sample_mode="all"`. Pass the result into `TaskRequest`:

```python
agent_difficulty: str | None = None
if evaluation_type == "model" and "agent_difficulty" in payload:
    raise ValueError("agent_difficulty is only valid for agent evaluations")
if evaluation_type == "agent":
    agent_difficulty = str(payload.get("agent_difficulty", "all"))
    if agent_difficulty not in {"all", "easy", "medium", "hard"}:
        raise ValueError("agent_difficulty must be one of: all, easy, medium, hard")
```

After parsing `sample_mode`, add:

```python
if evaluation_type == "agent" and sample_mode != "all":
    raise ValueError("agent sample_mode must be all")
```

- [ ] **Step 4: Run focused API/repository tests and confirm GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add a failing worker propagation assertion**

Update `test_evaluation_process_dispatches_agent_request` to construct the request with `sample_mode="all"` and `agent_difficulty="hard"`, return `total_samples=2`, and replace the old limit assertion with:

```python
assert observed["difficulty"] == "hard"
assert "limit" not in observed
```

- [ ] **Step 6: Run the worker test and confirm RED**

```bash
PYTHONPATH=src /Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m pytest \
  tests/test_task_executor.py::test_evaluation_process_dispatches_agent_request -q
```

Expected: FAIL because the executor still passes `limit=3`.

- [ ] **Step 7: Forward difficulty and leave model limit handling untouched**

Keep the existing `limit` selection only inside the model branch. Call Agent Benchmark with:

```python
result = run_codex_agent_benchmark(
    job_id=task_id,
    model=request.model,
    base_url=request.base_url,
    difficulty=request.agent_difficulty or "all",
    on_progress=report_progress,
    on_trace=report_trace,
)
```

Do not add another adapter, executor class, or registry.

- [ ] **Step 8: Run task API, executor, repository, and service tests**

```bash
PYTHONPATH=src /Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m pytest \
  tests/test_task_api.py tests/test_task_executor.py tests/test_task_repository.py \
  tests/test_task_service.py -q
```

Expected: PASS.

- [ ] **Step 9: Update the API contract and commit**

Document `agent_difficulty`, the four allowed values, the fixed Agent `sample_mode="all"`,
`benchmark_version`, sample difficulty fields, and `difficulty_report` in
`docs/architecture/20260804_API接口草案.md`.

```bash
git add src/evalhub/tasks/models.py src/evalhub/server.py src/evalhub/tasks/executor.py \
  tests/test_task_api.py tests/test_task_executor.py tests/test_task_repository.py \
  docs/architecture/20260804_API接口草案.md
git commit -m "feat: accept agent difficulty selection"
```

---

### Task 3: Agent difficulty selector in the existing form

**Files:**
- Modify: `frontend/src/types.ts:1-70`
- Modify: `frontend/src/lib/evaluation.ts:1-42`
- Modify: `frontend/src/lib/evaluation.test.ts:1-42`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx:1-165,307-437`
- Modify: `frontend/src/App.test.tsx:620-650`

**Interfaces:**
- Consumes: Task 2 API field `agent_difficulty` and fixed Agent `sample_mode="all"`.
- Produces: `AgentDifficulty`, `EvaluationFormValues.agentDifficulty`, a four-option accessible selector, and correctly serialized Agent requests.

- [ ] **Step 1: Add a failing request-builder test**

Define `agentDifficulty: "all"` in `baseValues`. In the Agent test, pass `agentDifficulty: "hard"` and expect:

```typescript
{
  evaluation_type: "agent",
  agent_framework: "codex",
  agent_difficulty: "hard",
  dataset: "coding_mini",
  adapter: "ollama",
  model: baseValues.model,
  base_url: baseValues.baseUrl,
  sample_mode: "all",
}
```

- [ ] **Step 2: Run the request-builder test and confirm RED**

```bash
cd frontend && npm test -- --run src/lib/evaluation.test.ts
```

Expected: TypeScript/test failure because the form values and request do not include Agent difficulty.

- [ ] **Step 3: Add the minimum TypeScript types and request mapping**

In `types.ts`:

```typescript
export type AgentDifficulty = "all" | "easy" | "medium" | "hard";
```

Add `agent_difficulty?: AgentDifficulty` to `EvaluationRequest`. Add
`agentDifficulty: AgentDifficulty` to `EvaluationFormValues`; serialize it and change Agent
`sample_mode` from `quick` to `all`.

- [ ] **Step 4: Run the request-builder test and confirm GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add a failing form interaction assertion**

In the existing App Agent submission test, click the accessible radio named `困难 · 2 道`, submit,
and expect the request to contain:

```typescript
agent_difficulty: "hard",
sample_mode: "all",
```

- [ ] **Step 6: Run the focused App test and confirm RED**

```bash
cd frontend && npm test -- --run src/App.test.tsx
```

Expected: FAIL because the difficulty control does not exist.

- [ ] **Step 7: Render four native radios and update Agent copy**

Add state:

```typescript
const [agentDifficulty, setAgentDifficulty] = useState<AgentDifficulty>("all");
```

Add it to `values`. Change `changeEvaluationType` to set `sampleMode("all")`. Reuse the existing
radio-card markup with this constant:

```typescript
const agentDifficulties: Array<{
  value: AgentDifficulty;
  label: string;
  count: string;
}> = [
  { value: "all", label: "全部", count: "6 道" },
  { value: "easy", label: "简单", count: "2 道" },
  { value: "medium", label: "中等", count: "2 道" },
  { value: "hard", label: "困难", count: "2 道" },
];
```

Each radio uses `name="agent-difficulty"` and `aria-label={`${option.label} · ${option.count}`}`.
Place the fieldset above “本次 Agent 评测流程”. Change “3 个隐藏校验编码任务” to
“6 个三级难度隐藏校验任务” and the footer to
`运行${agentDifficulty === "all" ? "全部 6 道" : "所选难度 2 道"} Coding Mini 任务；最终消息不会直接参与得分。`

- [ ] **Step 8: Run form/request tests and typecheck**

```bash
cd frontend && npm test -- --run src/lib/evaluation.test.ts src/App.test.tsx
cd frontend && npm run typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit the form change**

```bash
git add frontend/src/types.ts frontend/src/lib/evaluation.ts \
  frontend/src/lib/evaluation.test.ts frontend/src/components/dashboard/EvaluationForm.tsx \
  frontend/src/App.test.tsx
git commit -m "feat: select agent benchmark difficulty"
```

---

### Task 4: Per-tier result and failed-sample difficulty display

**Files:**
- Modify: `frontend/src/types.ts:94-152`
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.tsx:1-95`
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.test.tsx:29-52`

**Interfaces:**
- Consumes: Task 1 `difficulty_report`, `benchmark_version`, `requested_difficulty`, and failed-example `difficulty`.
- Produces: optional backward-compatible TypeScript result fields and an inline per-tier report.

- [ ] **Step 1: Add a failing result rendering test**

Add an Agent result fixture with:

```typescript
evaluation_type: "agent",
benchmark_version: "coding-mini-v2",
requested_difficulty: "all",
difficulty_report: [
  { difficulty: "easy", total: 2, passed: 1, pass_rate: 0.5 },
  { difficulty: "medium", total: 2, passed: 0, pass_rate: 0 },
  { difficulty: "hard", total: 2, passed: 0, pass_rate: 0 },
],
failed_examples: [
  {
    sample_id: "inventory_reservation",
    difficulty: "medium",
    score: 0,
    input: "inventory_reservation",
    prediction: "",
    reference: "hidden verifier passed",
    reason: "AssertionError",
  },
],
```

Assert visible `难度表现`, `简单`, `1 / 2`, `50%`, and failed-example badge `中等`.

- [ ] **Step 2: Run the result test and confirm RED**

```bash
cd frontend && npm test -- --run src/components/dashboard/EvaluationResultDetail.test.tsx
```

Expected: type/test failure because the result types and UI do not expose difficulty.

- [ ] **Step 3: Add optional result types for old-report compatibility**

Add:

```typescript
export interface AgentDifficultyResult {
  difficulty: Exclude<AgentDifficulty, "all">;
  total: number;
  passed: number;
  pass_rate: number;
}
```

Add optional `difficulty` to `FailedExample`; add optional `difficulty` and `difficulty_reason` to
`AgentSampleResult`; add optional
`benchmark_version`, `requested_difficulty`, and `difficulty_report` to `EvaluationResult`.

- [ ] **Step 4: Render the report inline without a new component**

Add one module-level label record:

```typescript
const difficultyLabels = { easy: "简单", medium: "中等", hard: "困难" } as const;
```

Below the Agent metadata strip, map `result.difficulty_report` into a semantic section titled
“难度表现”; show label, `${passed} / ${total}`, and `formatPassRate(passed, total)`. In each failed
example header, render a neutral `Badge` with `difficultyLabels[example.difficulty]` only when the
optional field exists. Do not create another route, chart, hook, or polling path.

- [ ] **Step 5: Run result tests and typecheck**

```bash
cd frontend && npm test -- --run src/components/dashboard/EvaluationResultDetail.test.tsx
cd frontend && npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit the result display**

```bash
git add frontend/src/types.ts frontend/src/components/dashboard/EvaluationResultDetail.tsx \
  frontend/src/components/dashboard/EvaluationResultDetail.test.tsx
git commit -m "feat: show agent difficulty results"
```

---

### Task 5: Documentation, complete verification, real run, and minimality review

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `docs/product/20260804_Agent评测路线图.md`
- Modify: `docs/superpowers/specs/2026-08-04-agent-benchmark-difficulty-design.md`

**Interfaces:**
- Consumes: completed API, backend result, frontend selector, and existing local startup script.
- Produces: verified user workflow and documentation matching implemented behavior.

- [ ] **Step 1: Update behavior documentation**

Document these exact behaviors:

- Coding Mini v2 contains six tasks, two per difficulty;
- Agent form can run all/easy/medium/hard;
- scoring still comes only from final workspace hidden verification;
- results expose total and per-tier pass rates;
- trace shows difficulty, tool activity, workspace changes, verifier evidence, and outcome;
- two tasks per tier are a smoke-scale comparison, not a statistically calibrated public leaderboard.

Change the design document status from “方案已确认，待书面审阅” to “已实现并验证” only after all
verification steps below pass.

- [ ] **Step 2: Run backend static and unit checks**

```bash
/Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m ruff check .
PYTHONPATH=src /Users/nedonion/PycharmProjects/evalhub/.venv/bin/python -m pytest
```

Expected: Ruff passes and all pytest tests pass. If local-port tests are denied by the sandbox,
rerun the same pytest command with the required local-network permission; do not skip them.

- [ ] **Step 3: Run all frontend checks**

```bash
cd frontend && npm test -- --run
cd frontend && npm run typecheck
cd frontend && npm run build
```

Expected: all Vitest files pass, TypeScript exits 0, and Vite produces a successful build.

- [ ] **Step 4: Run repository hygiene checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only intended documentation changes remain before the final commit.

- [ ] **Step 5: Run a real all-difficulty Agent evaluation**

Start from the isolated worktree with the repository virtualenv:

```bash
PYTHON=/Users/nedonion/PycharmProjects/evalhub/.venv/bin/python ./scripts/start_local.sh
```

Using the existing Browser plugin, select Agent evaluation, `全部 · 6 道`, and the installed
`qwen2.5:0.5b` model. Confirm while running that events appear before completion. At terminal state,
confirm 6/6 completed, each difficulty report has total 2, trace sample starts display difficulty,
refresh preserves events/results, and the browser console has no warning/error. Stop the local service
after validation.

- [ ] **Step 6: Run Ponytail over-engineering review on the branch diff**

Invoke `ponytail:ponytail-review` against `git diff 05d3e05...HEAD` plus the uncommitted documentation
diff. Accept only findings that delete unnecessary abstraction or duplication without removing explicit
validation, security, accessibility, audit evidence, or required tests. Rerun affected focused tests after
any accepted simplification.

- [ ] **Step 7: Commit documentation and verification status**

```bash
git add README.md docs/getting-started/20260804_本地运行指南.md \
  docs/product/20260804_Agent评测路线图.md \
  docs/superpowers/specs/2026-08-04-agent-benchmark-difficulty-design.md
git commit -m "docs: explain agent difficulty evaluation"
```

- [ ] **Step 8: Audit the original objective before integration**

Build an evidence table for: auditable, observable, explainable, and difficulty-tiered. Cite persisted
events/API response, running UI/refresh behavior, per-sample outcome plus verifier evidence, and the real
six-task tier report respectively. Do not claim the objective complete if any evidence is absent.

Because the main checkout currently contains unrelated uncommitted changes, do not merge or cherry-pick
automatically. Report the clean feature branch and the exact integration conflict risk to the user.
