# Prominent Model Services Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a prominent model-services workspace where users configure a provider, default model, and connectivity before starting API evaluations.

**Architecture:** Keep provider credentials and discovery on the existing backend APIs. Lift the selected provider and model into `App`, persist only those two non-secret identifiers in `localStorage`, and pass the same state to a dedicated page, overview status, sidebar badge, and the evaluation form.

**Tech Stack:** React 19, TypeScript 7, native `localStorage`, native `datalist`, Vitest, Testing Library, existing Tailwind CSS utilities.

## Global Constraints

- Preserve all existing uncommitted workspace changes and the safety stash `30c8ae82`; stage only files named by the current task.
- Do not add dependencies, backend routes, database fields, provider-specific SDKs, or periodic health checks.
- Never store an API Key, ciphertext, Base URL, or connection error in `localStorage`.
- Keep API Key inputs blank after save and use `type="password"` plus `autoComplete="new-password"`.
- Invalid local preference JSON, a deleted provider, or a provider switch must clear the stale model and fall back to an unconfigured state.
- Keep Agent evaluations restricted to Ollama and preserve existing API Benchmark support rules.
- Use visible labels, semantic status text, keyboard focus styles, and a single-column mobile fallback.
- Follow red-green-refactor: every production behavior is preceded by a test that fails for the expected missing behavior.

---

### Task 1: Make Provider Settings Page-Ready

**Files:**
- Modify: `frontend/src/components/dashboard/ProviderSettings.tsx`
- Modify: `frontend/src/components/dashboard/ProviderSettings.test.tsx`

**Interfaces:**
- Produces: `ProviderSettings` optional props `initialProviderId?: string | null` and `expanded?: boolean`.
- Preserves: `model`, `onModelChange`, and `onSelectionChange` as the controlled default-combination boundary.
- Produces: successful connection copy in the form `连接成功 · <milliseconds> ms · 发现 <count> 个模型`.

- [ ] **Step 1: Add failing tests for preferred provider, expanded mode, provider switching, and latency**

Add these behaviors to `ProviderSettings.test.tsx`:

```tsx
it("opens the preferred provider as an expanded setup page", async () => {
  render(
    <ProviderSettings
      initialProviderId="siliconflow"
      expanded
      model="Qwen/Qwen3-8B"
      onModelChange={vi.fn()}
      onSelectionChange={vi.fn()}
    />,
  );

  expect(await screen.findByLabelText("API 服务商")).toHaveValue("siliconflow");
  expect(screen.getByLabelText(/API Key/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "管理服务商" })).not.toBeInTheDocument();
});

it("clears a stale model when the provider changes", async () => {
  const user = userEvent.setup();
  const onModelChange = vi.fn();
  render(
    <ProviderSettings
      model="deepseek-v4-pro"
      onModelChange={onModelChange}
      onSelectionChange={vi.fn()}
    />,
  );

  await user.selectOptions(await screen.findByLabelText("API 服务商"), "siliconflow");
  expect(onModelChange).toHaveBeenLastCalledWith("");
});

it("reports connection latency and discovered model count", async () => {
  const user = userEvent.setup();
  render(
    <ProviderSettings
      expanded
      model="deepseek-v4-pro"
      onModelChange={vi.fn()}
      onSelectionChange={vi.fn()}
    />,
  );

  await screen.findByLabelText("API 服务商");
  const now = vi.spyOn(performance, "now")
    .mockReturnValueOnce(100)
    .mockReturnValueOnce(342);
  await user.click(screen.getByRole("button", { name: "保存并测试连接" }));
  expect(await screen.findByText("连接成功 · 242 ms · 发现 2 个模型")).toBeInTheDocument();
  now.mockRestore();
});
```

Update every existing test that clicks “保存并验证” to use “保存并测试连接”.

- [ ] **Step 2: Run the component test and verify RED**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ProviderSettings.test.tsx`

Expected: FAIL because the new props, automatic model clear, button copy, and latency notice do not exist.

- [ ] **Step 3: Implement the minimum page-ready behavior**

Update the prop contract and initial selection:

```tsx
interface ProviderSettingsProps {
  initialProviderId?: string | null;
  expanded?: boolean;
  model: string;
  modelError?: string;
  providerError?: string;
  onModelChange: (model: string) => void;
  onSelectionChange: (provider: ModelProvider | null) => void;
}

const [selectedId, setSelectedId] = useState(initialProviderId || "deepseek");
const [managing, setManaging] = useState(expanded);
```

When providers load, prefer `initialProviderId`; when a user selects a different provider call `onModelChange("")`. Hide the “管理服务商” button when `expanded` is true and keep the editor visible after reset.

Measure only the existing test request:

```tsx
const startedAt = performance.now();
const discovered = await testModelProvider(saved.id);
const elapsedMs = Math.max(0, Math.round(performance.now() - startedAt));
setModels(discovered);
setNotice(`连接成功 · ${elapsedMs} ms · 发现 ${discovered.length} 个模型`);
```

Rename the action to “保存并测试连接”. Do not change API calls or persist timing.

- [ ] **Step 4: Run the component test and verify GREEN**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ProviderSettings.test.tsx`

Expected: all provider settings tests pass with no console warnings.

- [ ] **Step 5: Commit Task 1 only**

```bash
git add frontend/src/components/dashboard/ProviderSettings.tsx frontend/src/components/dashboard/ProviderSettings.test.tsx
git commit -m "feat: prepare provider settings workspace"
```

---

### Task 2: Add the Model Services Workspace and Persistent Default

**Files:**
- Create: `frontend/src/components/dashboard/ModelServicesPanel.tsx`
- Create: `frontend/src/components/dashboard/ModelServicesPanel.test.tsx`
- Modify: `frontend/src/components/dashboard/SidebarNav.tsx`
- Modify: `frontend/src/components/dashboard/SidebarNav.test.tsx`
- Modify: `frontend/src/components/dashboard/OverviewPanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Changes: `WorkspaceView` adds `"model-services"`.
- Produces: `ModelServicesPanel({ providerId, provider, model, onProviderChange, onModelChange })`.
- Changes: `SidebarNav` receives `modelProviderReady: boolean`.
- Changes: `OverviewPanel` receives `modelProviderReady`, `modelProviderLabel`, and existing `onNavigate`.
- Persists: `localStorage["evalhub.api-model-default"] = {"providerId": string | null, "model": string}`.

- [ ] **Step 1: Add a failing standalone panel test**

Create `ModelServicesPanel.test.tsx` with the provider API mocked as in `ProviderSettings.test.tsx`:

```tsx
it("shows the full provider workflow without an extra management click", async () => {
  render(
    <ModelServicesPanel
      providerId="deepseek"
      provider={deepseek}
      model="deepseek-v4-pro"
      onProviderChange={vi.fn()}
      onModelChange={vi.fn()}
    />,
  );

  expect(screen.getByRole("heading", { name: "默认 API 模型" })).toBeInTheDocument();
  expect(await screen.findByLabelText("API 服务商")).toHaveValue("deepseek");
  expect(screen.getByLabelText("模型 ID")).toHaveValue("deepseek-v4-pro");
  expect(screen.getByLabelText(/API Key/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Add failing App and sidebar tests**

Extend the navigation helper union with `"模型服务"`, clear `localStorage` in `beforeEach`, and add:

```tsx
it("opens a prominent model services workspace and restores the saved default", async () => {
  const user = userEvent.setup();
  localStorage.setItem(
    "evalhub.api-model-default",
    JSON.stringify({ providerId: "deepseek", model: "deepseek-v4-pro" }),
  );
  render(<App />);

  await waitFor(() => expect(navigationButton("模型服务")).toHaveTextContent("已配置"));
  await user.click(navigationButton("模型服务"));
  expect(screen.getByRole("heading", { level: 1, name: "模型服务" })).toBeInTheDocument();
  expect(await screen.findByLabelText("API 服务商")).toHaveValue("deepseek");
  expect(screen.getByLabelText("模型 ID")).toHaveValue("deepseek-v4-pro");
});

it("falls back safely when the saved preference is invalid", async () => {
  localStorage.setItem("evalhub.api-model-default", "not-json");
  render(<App />);

  expect(navigationButton("模型服务")).toHaveTextContent("未配置");
  const status = screen.getByRole("region", { name: "模型服务状态" });
  expect(within(status).getByText("先配置一个 API 模型服务")).toBeInTheDocument();
});

it("clears a saved model when its provider no longer exists", async () => {
  localStorage.setItem(
    "evalhub.api-model-default",
    JSON.stringify({ providerId: "provider_deleted", model: "old-model" }),
  );
  render(<App />);

  await waitFor(() => expect(navigationButton("模型服务")).toHaveTextContent("未配置"));
  expect(JSON.parse(localStorage.getItem("evalhub.api-model-default") || "{}")).toEqual({
    providerId: "deepseek",
    model: "",
  });
});

it("persists a selected provider and model and exposes the overview shortcut", async () => {
  const user = userEvent.setup();
  render(<App />);

  const status = screen.getByRole("region", { name: "模型服务状态" });
  await user.click(within(status).getByRole("button", { name: "配置模型服务" }));
  expect(screen.getByRole("heading", { level: 1, name: "模型服务" })).toBeInTheDocument();
  await user.type(await screen.findByLabelText("模型 ID"), "deepseek-v4-pro");
  await waitFor(() => {
    expect(JSON.parse(localStorage.getItem("evalhub.api-model-default") || "{}")).toEqual({
      providerId: "deepseek",
      model: "deepseek-v4-pro",
    });
  });
});
```

Update `SidebarNav.test.tsx` to pass `modelProviderReady={false}` and assert the always-visible model-services button.

- [ ] **Step 3: Run the new workspace tests and verify RED**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ModelServicesPanel.test.tsx src/components/dashboard/SidebarNav.test.tsx src/App.test.tsx`

Expected: FAIL because the view, component, persistence, badge, and overview status do not exist.

- [ ] **Step 4: Implement the standalone panel**

Create a focused wrapper that keeps provider CRUD in `ProviderSettings`:

```tsx
interface ModelServicesPanelProps {
  providerId: string | null;
  provider: ModelProvider | null;
  model: string;
  onProviderChange: (provider: ModelProvider | null) => void;
  onModelChange: (model: string) => void;
}

export function ModelServicesPanel(props: ModelServicesPanelProps) {
  const ready = Boolean(props.provider?.key_configured && props.model.trim());
  return (
    <Panel aria-labelledby="model-services-title" className="overflow-hidden">
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-5 sm:px-6">
        <div>
          <h2 id="model-services-title" className="text-base font-semibold text-ink">
            默认 API 模型
          </h2>
          <p className="mt-1 text-sm text-muted">
            {ready ? `${props.provider!.name} · ${props.model}` : "先配置一个 API 模型服务"}
          </p>
        </div>
        <span className={ready ? "text-emerald-700" : "text-amber-700"}>
          {ready ? "已配置" : "未配置"}
        </span>
      </div>
      <div className="p-5 sm:p-6">
        <ProviderSettings
          initialProviderId={props.providerId}
          expanded
          model={props.model}
          onModelChange={props.onModelChange}
          onSelectionChange={props.onProviderChange}
        />
      </div>
    </Panel>
  );
}
```

Use the existing white panels, blue accent, monospace endpoint text, and one semantic green/amber readiness label. Do not add a modal, custom dropdown, animation, or new CSS file.

- [ ] **Step 5: Implement navigation, overview status, and App-owned persistence**

Add the view immediately after overview. In `App.tsx`, use one defensive initializer:

```tsx
const API_MODEL_PREFERENCE_KEY = "evalhub.api-model-default";

function readApiModelPreference(): { providerId: string | null; model: string } {
  try {
    const parsed = JSON.parse(localStorage.getItem(API_MODEL_PREFERENCE_KEY) || "null");
    return typeof parsed?.providerId === "string" && typeof parsed?.model === "string"
      ? parsed
      : { providerId: null, model: "" };
  } catch {
    return { providerId: null, model: "" };
  }
}
```

Keep `apiProvider` separate from the stored identifiers. When `onSelectionChange` receives a provider whose ID differs from the stored ID, clear the model. Persist only `{providerId, model}` from a `useEffect`.

Render `ModelServicesPanel` in a hidden workspace div, pass readiness to `SidebarNav`, and add a compact overview status strip with `aria-label="模型服务状态"` that navigates to `model-services`. The page copy is:

```tsx
"model-services": {
  eyebrow: "Model services",
  title: "模型服务",
  description: "先配置服务商、默认模型与连接状态，再发起 API 评测。",
}
```

Keep the model-services badge visible at all viewport widths; existing evaluation, asset, and result activity badges may retain their current `xl` visibility rule.

- [ ] **Step 6: Run workspace tests and verify GREEN**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ModelServicesPanel.test.tsx src/components/dashboard/SidebarNav.test.tsx src/App.test.tsx`

Expected: all selected tests pass and invalid storage does not raise a console error.

- [ ] **Step 7: Commit Task 2 only**

```bash
git add frontend/src/components/dashboard/ModelServicesPanel.tsx frontend/src/components/dashboard/ModelServicesPanel.test.tsx frontend/src/components/dashboard/SidebarNav.tsx frontend/src/components/dashboard/SidebarNav.test.tsx frontend/src/components/dashboard/OverviewPanel.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat: add prominent model services workspace"
```

---

### Task 3: Reuse the Default Combination in Evaluation Setup

**Files:**
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Changes: `EvaluationForm` receives `apiProvider: ModelProvider | null`, `apiModel: string`, and `onManageModelServices: () => void`.
- Removes: evaluation-form-owned `apiProvider`, `apiModel`, and embedded `ProviderSettings` state.
- Preserves: evaluation requests contain `provider_id`, `model`, and Base URL but never credentials.

- [ ] **Step 1: Replace the existing API submission test with a failing default-reuse test**

Update the App test to configure storage before rendering and assert the summary:

```tsx
it("reuses the configured default API model without another credential form", async () => {
  const user = userEvent.setup();
  vi.mocked(createEvaluation).mockResolvedValue(pendingTask);
  localStorage.setItem(
    "evalhub.api-model-default",
    JSON.stringify({ providerId: "deepseek", model: "deepseek-v4-pro" }),
  );
  render(<App />);

  await user.click(navigationButton("发起评测"));
  await user.selectOptions(await screen.findByLabelText("模型适配器"), "openai-compatible");
  const summary = await screen.findByRole("region", { name: "默认 API 模型" });
  expect(within(summary).getByText("DeepSeek")).toBeInTheDocument();
  expect(within(summary).getByText("deepseek-v4-pro")).toBeInTheDocument();
  expect(within(summary).queryByLabelText(/API Key/)).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "发起评测" }));
  expect(createEvaluation).toHaveBeenCalledWith(expect.objectContaining({
    adapter: "openai-compatible",
    provider_id: "deepseek",
    model: "deepseek-v4-pro",
    base_url: "https://api.deepseek.com",
  }));
});
```

Add one assertion that “修改模型服务” navigates to the new page.

- [ ] **Step 2: Run the App API test and verify RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx -t "reuses the configured default API model"`

Expected: FAIL because the evaluation form still owns and renders `ProviderSettings`.

- [ ] **Step 3: Lift API props into EvaluationForm and render a summary**

Remove the `ProviderSettings` import, `useCallback`, and local API provider/model state. Extend the props:

```tsx
apiProvider: ModelProvider | null;
apiModel: string;
onManageModelServices: () => void;
```

Add these three fields to the existing `EvaluationFormProps` without renaming or removing its dataset, Benchmark, Ollama, preparation, and submit fields.

Keep `missingApiConfiguration` and `EvaluationFormValues` based on these props. Replace the embedded editor with a semantic summary:

```tsx
<section aria-label="默认 API 模型">
  {apiProvider?.key_configured && apiModel.trim() ? (
    <>
      <p>{apiProvider.name}</p>
      <p className="font-mono">{apiModel}</p>
    </>
  ) : (
    <p>尚未配置默认 API 模型</p>
  )}
  <Button type="button" variant="secondary" onClick={onManageModelServices}>
    修改模型服务
  </Button>
</section>
```

In `App`, pass the shared provider/model and navigate to `model-services`. Do not change the existing supported-Benchmark gating or Agent fallback.

- [ ] **Step 4: Run the focused App test and verify GREEN**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx -t "reuses the configured default API model"`

Expected: PASS and the request contains no `api_key` field.

- [ ] **Step 5: Run all affected frontend tests**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/ProviderSettings.test.tsx src/components/dashboard/ModelServicesPanel.test.tsx src/components/dashboard/SidebarNav.test.tsx src/lib/evaluation.test.ts src/App.test.tsx`

Expected: all selected tests pass without warnings.

- [ ] **Step 6: Commit Task 3 only**

```bash
git add frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat: reuse default API model in evaluations"
```

---

### Task 4: Full Verification and Local Runtime Check

**Files:**
- Verify only; no additional production files.

**Interfaces:**
- Confirms: the frontend bundle exposes the new navigation and the existing backend provider endpoint remains unchanged.

- [ ] **Step 1: Run the full frontend suite**

Run: `npm --prefix frontend run test:run`

Expected: all Vitest files pass with zero failures.

- [ ] **Step 2: Run the production build**

Run: `npm --prefix frontend run build`

Expected: TypeScript and Vite exit 0, and `frontend/dist` is regenerated.

- [ ] **Step 3: Run Python regression checks**

Run: `.venv/bin/python -m ruff check .`

Run: `.venv/bin/python -m pytest`

Expected: Ruff has zero findings and pytest has zero failures; the existing Docker integration skips remain allowed.

- [ ] **Step 4: Verify repository hygiene and preserved local work**

Run: `git diff --check`

Run: `git status --short`

Run: `git stash list`

Expected: no whitespace errors, the pre-existing local changes and five untracked protocol/evaluator files remain, and `codex-preserve-before-main-sync-2026-08-05` still exists.

- [ ] **Step 5: Rebuild and restart the local console**

Run: `.venv/bin/python scripts/stop_existing_evalhub.py --host 127.0.0.1 --port 8000`

Run: `.venv/bin/python run_evalhub.py serve --host 127.0.0.1 --port 8000`

Expected: the new process reports `EvalHub local console: http://127.0.0.1:8000`.

- [ ] **Step 6: Verify the live endpoint and served bundle**

Run: `curl -i http://127.0.0.1:8000/api/model-providers`

Expected: HTTP 200 with DeepSeek, 硅基流动, and Kimi entries and no credential values.

Open `http://127.0.0.1:8000`, then verify the sidebar shows “模型服务”, the page opens without an extra management click, and an API evaluation displays the configured default summary.
