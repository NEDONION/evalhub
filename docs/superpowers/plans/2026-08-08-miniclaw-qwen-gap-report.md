# MiniClaw qwen3:4b Gap Experiment and README Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one valid `coding-mini-v3` 6-sample evaluation with MiniClaw + `qwen3:4b`, compare it with the frozen Pi baseline, and publish every formal or excluded MiniClaw run in a dedicated README chapter.

**Architecture:** Keep EvalHub and MiniClaw configuration unchanged on disk. A temporary launcher starts the existing EvalHub server with process-only MiniClaw model overrides pointing to Ollama's OpenAI-compatible endpoint; the normal server is restored after the result is persisted. README values come only from persisted task JSON and the frozen Pi matrix, with infrastructure failures separated from capability results.

**Tech Stack:** Python 3.11+, MiniClaw 0.1.0, Ollama OpenAI-compatible API, EvalHub task API, SQLite-backed persisted tasks, Markdown.

## Global Constraints

- Use the frozen `coding-mini-v3` six-sample suite and scaffold hash `d8a7cbaf0d432018`.
- Use `qwen3:4b`; its frozen Pi baseline is 1/6, not the older cross-version 2/6 task.
- Do not write MiniClaw `.env`, config, memory, Skills, or credentials.
- Keep write/edit approval restricted to the current EvalHub sample workspace; do not approve commands, network, memory, or outside paths.
- Accept the first formal six-sample run that completes without infrastructure failure; never rerun based on score.
- Record protocol or infrastructure failures in an excluded-runs table and never mix them into the capability ranking.
- Preserve all unrelated uncommitted and staged MiniClaw changes; do not stage or commit that repository.

---

### Task 1: Verify the local comparison boundary

**Files:**
- Read: `.runtime/agent-model-matrix-v3/qwen3-4b.json`
- Read: `/Users/nedonion/PycharmProjects/miniclaw/src/miniclaw/providers/openai_compatible.py`
- Read: `/Users/nedonion/PycharmProjects/miniclaw/tests/test_openai_compatible_provider.py`

**Interfaces:**
- Consumes: Ollama `GET /api/tags` and `GET /v1/models`; frozen Pi result JSON.
- Produces: evidence that `qwen3:4b` is installed, the OpenAI-compatible endpoint responds, and Pi scored 1/6 on `coding-mini-v3`.

- [ ] **Step 1: Confirm both repositories' current state without changing it**

Run `git status --short --branch` in EvalHub and MiniClaw. Expected: EvalHub contains only plan/design commits; MiniClaw may contain unrelated staged and unstaged user work that must remain untouched.

- [ ] **Step 2: Re-run the scoped MiniClaw Provider regression gate**

Run:

```bash
.venv/bin/python -m unittest tests.test_openai_compatible_provider -v
.venv/bin/ruff check src/miniclaw/providers/openai_compatible.py tests/test_openai_compatible_provider.py
git diff --check -- src/miniclaw/providers/openai_compatible.py tests/test_openai_compatible_provider.py
```

Expected: 13 Provider tests pass, Ruff passes, and the scoped diff has no whitespace errors.

- [ ] **Step 3: Verify the frozen Pi baseline**

Read `.runtime/agent-model-matrix-v3/qwen3-4b.json` and assert these literal facts: model `qwen3:4b`, benchmark `coding-mini-v3`, passed samples `1`, total samples `6`, scaffold hash `d8a7cbaf0d432018`.

- [ ] **Step 4: Verify Ollama availability without downloading anything**

Query `http://127.0.0.1:11434/api/tags` and `http://127.0.0.1:11434/v1/models`. Expected: both return successfully and include `qwen3:4b`.

### Task 2: Run the formal MiniClaw + qwen3:4b evaluation

**Files:**
- Create temporarily: `/tmp/evalhub_miniclaw_qwen_server.py`
- Persisted runtime output: `.runtime/evalhub.db`
- Persisted sample workspaces: the returned Job ID directory under `.runtime/agent-runs/`

**Interfaces:**
- Consumes: `evalhub.server.serve(host: str, port: int)`, MiniClaw process environment, `POST /api/evaluations`.
- Produces: one terminal EvalHub task with `agent.model=qwen3:4b` and six completed samples.

- [ ] **Step 1: Create a secret-free temporary launcher**

Use `apply_patch` to create `/tmp/evalhub_miniclaw_qwen_server.py`. It must insert EvalHub `src` into `sys.path`, set these process-only variables, and call `serve("127.0.0.1", 8765)`:

```python
os.environ["EVALHUB_MINICLAW_ROOT"] = "/Users/nedonion/PycharmProjects/miniclaw"
os.environ["MINICLAW_MODEL_NAME"] = "qwen3:4b"
os.environ["MINICLAW_MODEL_BASE_URL"] = "http://127.0.0.1:11434/v1"
os.environ["MINICLAW_MODEL_API_KEY_ENV"] = "EVALHUB_OLLAMA_MODEL_API_KEY"
os.environ["EVALHUB_OLLAMA_MODEL_API_KEY"] = "ollama-local"
```

The fixed local value is not a credential and must never be saved by EvalHub or MiniClaw.

- [ ] **Step 2: Stop the current default server cleanly**

Send Ctrl-C to the owned EvalHub server session on port 8765. Before switching, query `/api/evaluations` and confirm there are no `pending`, `running`, or `canceling` tasks.

- [ ] **Step 3: Start the temporary qwen server**

Run `.venv/bin/python /tmp/evalhub_miniclaw_qwen_server.py` from the EvalHub root in an owned PTY session.

- [ ] **Step 4: Verify the effective Agent metadata**

Query `GET /api/agents`. Expected MiniClaw fields: `available=true`, `version=0.1.0`, and `model=qwen3:4b`. Do not submit a task if any field differs.

- [ ] **Step 5: Submit exactly one formal six-sample task**

POST this literal body to `/api/evaluations`:

```json
{
  "evaluation_type": "agent",
  "agent_framework": "miniclaw",
  "dataset": "coding_mini",
  "sample_mode": "all",
  "agent_difficulty": "all"
}
```

Save the returned Job ID. Do not submit another task based on the eventual score.

- [ ] **Step 6: Monitor by state changes until terminal**

Poll `GET /api/evaluations/` followed by the Job ID returned in Step 5 every ten seconds, printing only status, completed samples, total samples, elapsed seconds, and safe error text. Continue until `success`, `failed`, or `canceled`.

- [ ] **Step 7: Classify validity before reading the score**

The task is a formal result only if all six samples completed and no task-level infrastructure error occurred. If invalid, preserve it as an excluded run, diagnose the boundary, and do not present it as model capability.

### Task 3: Restore the normal service and capture immutable evidence

**Files:**
- Delete after use: `/tmp/evalhub_miniclaw_qwen_server.py`
- Read: `.runtime/evalhub.db`
- Read: `.runtime/agent-model-matrix-v3/*.json`
- Read: `.worktrees/complete-agent-evaluation/.runtime/evalhub.db`

**Interfaces:**
- Consumes: the formal qwen task, MiniClaw Pro task `job_b760043b552b`, Flash task `job_08aa057bf2db`, excluded Flash task `job_4507d33dac62`, and frozen Pi matrix results.
- Produces: a non-sensitive result snapshot sufficient to write every README table.

- [ ] **Step 1: Extract the qwen public result fields**

Read only: Job ID, Agent version/model/fingerprint, benchmark version, passed/total, failed sample IDs, protocol preflight, execution summary, difficulty report, six capability dimensions, and per-sample status/diagnostics. Never copy final messages, prompts, API headers, or credentials.

- [ ] **Step 2: Stop the temporary server and restore default MiniClaw**

Stop the owned qwen server, then start:

```bash
EVALHUB_MINICLAW_ROOT=/Users/nedonion/PycharmProjects/miniclaw \
  .venv/bin/python run_evalhub.py serve --host 127.0.0.1 --port 8765
```

- [ ] **Step 3: Verify restoration and persistence**

Query `/api/agents` and the qwen Job ID. Expected: active MiniClaw model is `deepseek-v4-pro`; the saved task still reports result model `qwen3:4b` and its original score.

- [ ] **Step 4: Assemble the result source set**

Extract matching public aggregates for:

- MiniClaw Pro formal run `job_b760043b552b`.
- MiniClaw Flash formal run `job_08aa057bf2db`.
- MiniClaw qwen3:4b formal run from Task 2.
- Flash pre-fix excluded run `job_4507d33dac62`.
- Frozen Pi Pro, Flash, and qwen3:4b matrix JSON files.

- [ ] **Step 5: Remove the owned temporary launcher**

Delete `/tmp/evalhub_miniclaw_qwen_server.py` with `apply_patch`; do not delete runtime databases, sample workspaces, or evaluation evidence.

### Task 4: Publish the MiniClaw README chapter

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the public result source set from Task 3.
- Produces: anchor `#miniclaw-agent-evaluation-report` and four evidence-backed Markdown tables.

- [ ] **Step 1: Add the top navigation entry**

Add `MiniClaw 报告` linking to `#miniclaw-agent-evaluation-report` immediately after the existing `Agent 报告` link.

- [ ] **Step 2: Add the formal MiniClaw results table**

Insert `## MiniClaw Agent Evaluation Report` after `Agent Benchmark Report`. Explain the single-run rule and add a table with model, runtime, Job ID, protocol preflight, passed samples, failed samples, tool calls/errors, and average seconds per sample for Pro, Flash, and qwen3:4b.

- [ ] **Step 3: Add the six-dimension table**

Add planning, code understanding, implementation, tool use, verification, and robustness values on a 0–100 scale for all three formal MiniClaw runs. Convert persisted 0–1 scores by multiplying by 100; do not round beyond the precision already shown in persisted results.

- [ ] **Step 4: Add the same-model Pi comparison table**

For Pro, Flash, and qwen3:4b, show Pi passed/6, MiniClaw passed/6, Pi tool calls, MiniClaw tool calls, Pi average seconds, and MiniClaw average seconds. Describe observations without claiming causation from a six-task single run.

- [ ] **Step 5: Add excluded runs and reproducibility notes**

List `job_4507d33dac62` as excluded because the pre-fix Provider parser rejected SiliconFlow empty tool-name continuation fragments. State that its 0/6 is not a capability result. Record `coding-mini-v3`, MiniClaw 0.1.0, scaffold hash `d8a7cbaf0d432018`, hidden Verifier scoring, and that this is not official SWE-bench.

- [ ] **Step 6: Inspect the complete README section**

Read the navigation and both Agent report sections end-to-end. Confirm every number maps to persisted evidence, table labels distinguish Pi from MiniClaw, and no excluded score appears in ranking prose.

### Task 5: Verify and commit the report

**Files:**
- Modify: `README.md`
- Existing plan: `docs/superpowers/plans/2026-08-08-miniclaw-qwen-gap-report.md`

**Interfaces:**
- Consumes: completed README and unchanged EvalHub implementation.
- Produces: a verified local commit containing only the README report.

- [ ] **Step 1: Run EvalHub static and backend checks**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
git diff --check
```

Expected: Ruff passes, backend reports 435 passed and 9 skipped or a newer all-green count, and no whitespace errors exist.

- [ ] **Step 2: Run frontend checks**

Run:

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all frontend tests, TypeScript checking, and Vite production build pass.

- [ ] **Step 3: Re-run the MiniClaw scoped gate**

Run the 13 Provider tests, scoped Ruff, and scoped `git diff --check` from Task 1. Also report the known full-suite status without modifying unrelated staged or unstaged files.

- [ ] **Step 4: Confirm repository hygiene**

Verify EvalHub contains only the intended README change plus already committed design/plan history. Inspect the README diff for secrets, model responses, generated files, or invented values. Confirm MiniClaw unrelated changes remain untouched.

- [ ] **Step 5: Commit only the README report**

```bash
git add README.md
git commit -m "docs: 发布 MiniClaw 多模型评测报告"
```

Do not stage runtime artifacts or MiniClaw files. Do not push unless the user requests it.
