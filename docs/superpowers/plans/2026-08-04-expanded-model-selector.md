# Expanded Model Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩充模型评测与 Agent 评测的 Ollama 候选，并用按安装状态分组、展示用途与容量的独立富下拉替换原生模型 `select`。

**Architecture:** `evalhub.ollama.RECOMMENDED_OLLAMA_MODELS` 继续作为唯一目录，并向现有 `model_options` 增加评测用途与能力标签。React 的 `EvaluationForm` 按当前评测类型过滤目录，新的 `ModelSelector` 只负责可访问的展示与选择；未安装模型继续复用 App 现有资产下载流程。

**Tech Stack:** Python 3.11、标准库 `unittest`、React 19、TypeScript 7、Tailwind CSS 4、Lucide React、Vitest、Testing Library。

## Global Constraints

- 不新增 Python 或前端依赖。
- 不修改 Pi Runner、评分、Benchmark 或模型下载 API。
- 服务端推荐目录是唯一模型名单，前端不得硬编码第二份。
- API 只新增 `evaluation_types` 和 `capability_label`，既有字段保持兼容。
- 本机自定义模型默认支持 `model` 与 `agent`。
- `qwen2.5-coder:7b` 只支持 `model`。
- 已安装模型优先、推荐模型随后，按名称去重，实际容量优先。
- 未安装模型复用现有下载确认、进度、取消和提交阻塞流程。
- 保护现有无关修改，只暂存本计划文件。

---

### Task 1: Purpose-aware Ollama catalog

**Files:**
- Modify: `tests/test_ollama_status.py`
- Modify: `src/evalhub/ollama.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/hooks/useEvalHub.test.ts`

**Interfaces:**
- Produces: JSON `evaluation_types: list[str]` and `capability_label: str`.
- Produces: TypeScript `ModelOption.evaluation_types: EvaluationType[]` and `capability_label: string`.
- Preserves: `_build_model_options(installed_models, actual_sizes)` sorting and size precedence.

- [ ] **Step 1: Write the failing catalog tests**

Replace the five-model assertion with the approved ordered catalog:

```python
def test_recommended_catalog_separates_model_and_agent_candidates(self) -> None:
    """推荐目录应覆盖答题与 Agent 候选，并隔离不兼容工具模型。"""
    from evalhub.ollama import DEFAULT_OLLAMA_MODEL, RECOMMENDED_OLLAMA_MODELS

    catalog = {str(item["name"]): item for item in RECOMMENDED_OLLAMA_MODELS}
    self.assertEqual(DEFAULT_OLLAMA_MODEL, "granite4.1:3b")
    self.assertEqual(list(catalog), [
        "granite4.1:3b", "qwen3:4b", "qwen3:8b", "qwen3:14b",
        "ministral-3:8b", "gemma4:12b", "lfm2.5:8b",
        "north-mini-code-1.0:q4_K_M", "qwen2.5:0.5b", "qwen2.5:1.5b",
        "deepseek-r1:1.5b", "qwen2.5-coder:7b",
    ])
    self.assertEqual(catalog["qwen2.5-coder:7b"]["evaluation_types"], ["model"])
    self.assertEqual(catalog["ministral-3:8b"]["evaluation_types"], ["model", "agent"])
```

Add `custom-local:latest` to the fake installed response and assert:

```python
self.assertEqual(option_by_name["custom-local:latest"]["evaluation_types"], ["model", "agent"])
self.assertEqual(option_by_name["custom-local:latest"]["capability_label"], "本机模型")
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_ollama_status.py -q`

Expected: FAIL because the catalog still has five entries and options lack the new fields.

- [ ] **Step 3: Implement the catalog metadata**

Replace the five entries with the twelve exact models, labels, sizes and purposes from the approved spec. Every entry has this shape:

```python
{
    "name": "ministral-3:8b",
    "label": "Ministral 3 8B",
    "description": "原生函数调用与 Agent 工作流，适合平衡速度和任务完成率。",
    "estimated_size_bytes": 6_000_000_000,
    "evaluation_types": ["model", "agent"],
    "capability_label": "Agent 工具",
}
```

In `_model_option`, use recommendation values or these custom-model defaults:

```python
evaluation_types = list(recommended["evaluation_types"]) if recommended else ["model", "agent"]
capability_label = str(recommended["capability_label"]) if recommended else "本机模型"
```

Return both fields with the existing option dictionary. Keep signatures unchanged.

- [ ] **Step 4: Update TypeScript and fixtures**

Extend `ModelOption`:

```typescript
evaluation_types: EvaluationType[];
capability_label: string;
```

Add fields to every non-empty `model_options` fixture in `App.test.tsx` and `useEvalHub.test.ts`. Granite uses `["model", "agent"]` and `"Agent 基线"`; Qwen answer fixtures use `["model"]` and `"轻量答题"`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_ollama_status.py -q
npm --prefix frontend run typecheck
```

Expected: backend tests pass and TypeScript exits 0.

- [ ] **Step 6: Commit**

```bash
git add src/evalhub/ollama.py tests/test_ollama_status.py frontend/src/types.ts frontend/src/App.test.tsx frontend/src/hooks/useEvalHub.test.ts
git commit -m "feat: expand purpose-aware model catalog"
```

---

### Task 2: Accessible rich ModelSelector

**Files:**
- Create: `frontend/src/components/dashboard/ModelSelector.tsx`
- Create: `frontend/src/components/dashboard/ModelSelector.test.tsx`

**Interfaces:**
- Consumes: `ModelOption[]`, selected name and `formatBytes()`.
- Produces: `ModelSelector({ id, label, options, value, describedBy, onChange })`.
- Emits: the selected model name; download state stays in App.

- [ ] **Step 1: Write failing component tests**

Use an installed Granite and recommended Ministral fixture. Assert:

```tsx
await user.click(screen.getByRole("button", { name: "Agent 基模" }));
expect(screen.getByText("已安装")).toBeInTheDocument();
expect(screen.getByText("推荐下载")).toBeInTheDocument();
expect(screen.getByText("约 6.0 GB")).toBeInTheDocument();
await user.click(screen.getByRole("option", { name: /Ministral 3 8B/ }));
expect(onChange).toHaveBeenCalledWith("ministral-3:8b");
```

Add one keyboard test for ArrowDown, ArrowUp, Home, End, Enter and Escape. Add one empty-state test asserting a disabled “暂无可用模型” trigger.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ModelSelector.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the minimal component**

Public props:

```typescript
interface ModelSelectorProps {
  id: string;
  label: string;
  options: ModelOption[];
  value: string;
  describedBy?: string;
  onChange: (model: string) => void;
}
```

Implementation:

- Native button trigger with `aria-haspopup="listbox"`, expanded state, controls and description.
- Absolute listbox panel, non-empty “已安装” and “推荐下载” groups.
- Option buttons with `role="option"`, `aria-selected` and selected left blue rail.
- Existing Lucide `Check`, `ChevronDown`, `Download`, `HardDrive` icons.
- Existing `formatBytes`; “约” only for estimated sizes.
- One `open` state, one active index and one option-ref array; no generic dropdown abstraction.
- Arrow/Home/End focus movement, Enter/Space selection, Escape focus return, outside click close.
- Tailwind-only blue-gray trigger, compact capability pill, status text, visible focus and max-height scroll.

- [ ] **Step 4: Verify GREEN**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ModelSelector.test.tsx`

Expected: all selector tests pass without console warnings.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dashboard/ModelSelector.tsx frontend/src/components/dashboard/ModelSelector.test.tsx
git commit -m "feat: add rich model selector"
```

---

### Task 3: Evaluation filtering and download-flow integration

**Files:**
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `ModelOption.evaluation_types`, `ModelSelector`, existing callbacks.
- Produces: mode-filtered choices and a valid fallback on mode switch.

- [ ] **Step 1: Write failing App behavior tests**

Add a `chooseModel` helper that opens the labelled trigger and clicks a named option. Replace model `selectOptions` calls with it. Add a test that selects model-only Qwen, enters Agent mode, verifies Qwen is absent, Ministral is present, and installed Granite becomes selected.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL because the form still uses native select and has no purpose filtering.

- [ ] **Step 3: Integrate and filter**

In `EvaluationForm.tsx`:

```typescript
const applicableModels = availableModels.filter((option) =>
  option.evaluation_types.includes(evaluationType),
);
const selectedModelOption = applicableModels.find((option) => option.name === model);
```

Extend the fallback option with both evaluation types and “状态未知”. Add `modelForType(nextType)` that keeps a compatible current model, otherwise selects the first installed applicable model, otherwise the first applicable model. Invoke it from `changeEvaluationType` only when the name changes.

Replace the native model select with:

```tsx
<ModelSelector
  id="model"
  label={evaluationType === "agent" ? "Agent 基模" : "模型"}
  options={applicableModels}
  value={model}
  describedBy={missingOllamaModel ? "model-error" : undefined}
  onChange={onModelChange}
/>
```

Keep the existing error, asset link and submit blocking unchanged.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
npm --prefix frontend run test:run -- src/components/dashboard/ModelSelector.test.tsx src/App.test.tsx
npm --prefix frontend run typecheck
```

Expected: focused tests pass and TypeScript exits 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/App.test.tsx
git commit -m "feat: filter models by evaluation type"
```

---

### Task 4: Documentation and verification

**Files:**
- Modify: `docs/architecture/20260804_API接口草案.md` only in the model-option API section.
- Modify: `docs/getting-started/20260804_Ollama本地模型安装与验证.md`.

**Interfaces:**
- Documents: additive fields, mode filtering and recommended pull commands.

- [ ] **Step 1: Update docs**

Document this option example:

```json
{
  "name": "ministral-3:8b",
  "installed": false,
  "size_bytes": 6000000000,
  "size_kind": "estimated",
  "evaluation_types": ["model", "agent"],
  "capability_label": "Agent 工具"
}
```

Add pull commands for Ministral 3 8B, Gemma 4 12B, Qwen 3 14B and North Mini Code. State that `qwen2.5-coder:7b` is model-evaluation-only in this catalog.

- [ ] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
npm --prefix frontend run test:run
npm --prefix frontend run build
git diff --check
```

Expected: all configured tests pass, existing Docker integration skips remain skips, and build/lint/diff checks exit 0.

- [ ] **Step 3: Inspect desktop and mobile UI**

At 1440 px and 390 px, verify trigger/panel width, grouping, non-color status labels, mode fallback, keyboard behavior and viewport clipping.

- [ ] **Step 4: Commit docs only**

```bash
git add -p docs/architecture/20260804_API接口草案.md docs/getting-started/20260804_Ollama本地模型安装与验证.md
git commit -m "docs: document purpose-aware model choices"
```

- [ ] **Step 5: Audit scope**

Run `git status --short`, `git log --oneline -6` and `git diff HEAD~4 --stat`. Confirm only catalog, selector, integration and scoped documentation are committed; user-owned Harness, runtime and unrelated docs remain unstaged.

## Self-review

- Spec coverage: Tasks 1–4 cover catalog, API metadata, custom models, Qwen exclusion, rich selector, keyboard use, download reuse, empty/error states, docs and visual QA.
- Placeholder scan: every behavior-changing task names exact files, interfaces, commands and expected RED/GREEN results.
- Type consistency: backend `evaluation_types` maps to `EvaluationType[]`; selector emits only the existing model-name callback.
- Scope: one shared catalog and one consumer component form a single testable feature; no second subsystem is introduced.
