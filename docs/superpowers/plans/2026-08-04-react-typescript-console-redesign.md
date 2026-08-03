# React TypeScript Console Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder-heavy static frontend with a blue-white Vercel-inspired React + TypeScript evaluation console that exposes only working EvalHub capabilities.

**Architecture:** `frontend/` becomes a Vite React application. A typed API module owns all `/api/*` traffic, pure evaluation helpers own payload/validation rules, focused dashboard components render the page, and a small hook coordinates async state. Vite proxies API calls during development; the Python server serves `frontend/dist` after production builds.

**Tech Stack:** Vite, React, TypeScript, Tailwind CSS, Lucide React, Vitest, Testing Library, Python standard-library HTTP server.

## Global Constraints

- Use a blue-white palette: page `#F7F9FC`, surface `#FFFFFF`, primary `#2563EB`, primary hover `#1D4ED8`, text `#0F172A`, muted `#64748B`, border `#E2E8F0`, soft primary `#EFF6FF`.
- Preserve the existing `/api/health`, `/api/datasets`, `/api/ollama/status`, `/api/datasets/prepare`, and `/api/evaluations/run` contracts.
- Do not add routes or navigation entries for model registry, job history, reports, leaderboard, or release gates.
- Do not load fonts, icons, or UI assets from runtime CDNs.
- Keep all user-facing copy in Chinese except established technical names.
- Keep the current Python backend and evaluation engine behavior unchanged.
- Preserve unrelated worktree changes and stage only task-owned files in each commit.

---

## File Structure

- `frontend/package.json`: frontend commands and dependency manifest.
- `frontend/package-lock.json`: reproducible npm dependency graph.
- `frontend/vite.config.ts`: React plugin, Tailwind plugin, test environment, and `/api` proxy.
- `frontend/tsconfig.json`, `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json`: strict TypeScript configuration.
- `frontend/index.html`: Vite document shell with the React mount node.
- `frontend/src/main.tsx`: React entry point.
- `frontend/src/styles.css`: Tailwind import, design tokens, base rules, and restrained shared effects.
- `frontend/src/types.ts`: API and UI domain types.
- `frontend/src/lib/api.ts`: typed API calls and normalized errors.
- `frontend/src/lib/evaluation.ts`: payload construction, validation, labels, and score formatting.
- `frontend/src/lib/utils.ts`: class-name composition.
- `frontend/src/hooks/useEvalHub.ts`: refresh, prepare, run, loading, and module-specific error state.
- `frontend/src/components/ui/`: `Button`, `Badge`, `Panel`, and `FieldMessage` primitives.
- `frontend/src/components/dashboard/`: `Header`, `MetricStrip`, `OllamaPanel`, `EvaluationForm`, `DatasetTable`, and `ResultPanel`.
- `frontend/src/App.tsx`: dashboard composition and shared selection state.
- `frontend/src/test/setup.ts`: Testing Library matchers and cleanup.
- `frontend/src/**/*.test.ts(x)`: focused unit and integration tests.
- `src/evalhub/server.py`: static root selection for the Vite production build.
- `tests/test_server_frontend.py`: Python static-root behavior.
- `scripts/start_local.sh`: build-presence check and actionable frontend setup guidance.
- `README.md`, `docs/getting-started/LOCAL_RUN.md`: React development and production run instructions.

---

### Task 1: Vite React TypeScript Foundation

**Files:**
- Modify: `.gitignore`
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Replace: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/lib/utils.ts`
- Create: `frontend/src/lib/utils.test.ts`
- Create: `frontend/src/test/setup.ts`
- Delete: `frontend/app.js`
- Delete: `frontend/styles.css`

**Interfaces:**
- Produces: `cn(...inputs: ClassValue[]): string`
- Produces: npm scripts `dev`, `build`, `typecheck`, `test`, and `test:run`
- Produces: Vite proxy `/api -> http://127.0.0.1:8000`

- [ ] **Step 1: Create the package manifest and install the approved dependencies**

Add `node_modules/` to `.gitignore` so frontend dependencies never enter version control.

Create `frontend/package.json` with scripts:

```json
{
  "name": "evalhub-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "typecheck": "tsc -b --pretty false",
    "test": "vitest",
    "test:run": "vitest run"
  }
}
```

Run:

```bash
cd frontend
npm install react react-dom lucide-react clsx tailwind-merge
npm install -D vite @vitejs/plugin-react typescript @types/react @types/react-dom tailwindcss @tailwindcss/vite vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Expected: `frontend/package-lock.json` is generated and npm exits 0.

- [ ] **Step 2: Write the failing utility test**

Create `frontend/src/lib/utils.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("merges conditional and conflicting Tailwind classes", () => {
    expect(cn("px-2", false && "hidden", "px-4", { block: true })).toBe("px-4 block");
  });
});
```

- [ ] **Step 3: Run the utility test and verify the expected failure**

Run: `npm --prefix frontend run test:run -- src/lib/utils.test.ts`

Expected: FAIL because `./utils` does not exist.

- [ ] **Step 4: Add strict TypeScript, Vite, Tailwind, test setup, and the minimal React shell**

Implement `cn`:

```ts
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

Configure `vite.config.ts` with `react()`, `tailwindcss()`, `test.environment = "jsdom"`, `setupFiles = "./src/test/setup.ts"`, and the `/api` proxy. Configure strict TypeScript with DOM libraries and `noUncheckedIndexedAccess`. Replace the document shell with `<div id="root"></div>` and load `/src/main.tsx`.

In `styles.css`, import Tailwind and define the approved palette as CSS variables under `:root`; set system fonts, the blue-gray page background, text color, focus rings, and reduced-motion behavior. Create `App.tsx` with a temporary semantic `<main><h1>EvalHub</h1></main>` shell and render `<App />` from `main.tsx`.

- [ ] **Step 5: Run foundation verification**

Run:

```bash
npm --prefix frontend run test:run -- src/lib/utils.test.ts
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: utility test PASS, typecheck exits 0, and `frontend/dist/index.html` is generated.

- [ ] **Step 6: Commit the foundation**

```bash
git add .gitignore frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/index.html frontend/src frontend/app.js frontend/styles.css
git commit -m "build: scaffold React TypeScript frontend"
```

---

### Task 2: Typed API Boundary

**Files:**
- Create: `frontend/src/types.ts`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `Dataset`, `OllamaStatus`, `EvaluationResult`, `EvaluationRequest`, and `ModelOption` types
- Produces: `getHealth()`, `getDatasets()`, `getOllamaStatus(model, baseUrl)`, `prepareDataset(dataset)`, `runEvaluation(request)`
- Produces: `ApiError extends Error`

- [ ] **Step 1: Write failing API behavior tests**

Create tests that replace `global.fetch` with `vi.fn()` and assert:

```ts
it("encodes Ollama status query parameters", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okJson(ollamaFixture)));
  await getOllamaStatus("qwen 2.5:0.5b", "http://127.0.0.1:11434");
  expect(fetch).toHaveBeenCalledWith(
    "/api/ollama/status?model=qwen%202.5%3A0.5b&base_url=http%3A%2F%2F127.0.0.1%3A11434",
    expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
  );
});

it("converts unsuccessful JSON responses to ApiError", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(errorJson(500, "Ollama 不可用")));
  await expect(getHealth()).rejects.toMatchObject({ message: "Ollama 不可用", status: 500 });
});
```

Include a POST assertion that `runEvaluation` sends `JSON.stringify(request)`.

- [ ] **Step 2: Run tests and verify failure**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts`

Expected: FAIL because `types.ts` and `api.ts` do not exist.

- [ ] **Step 3: Implement exact response types and API functions**

Define fields currently emitted by `src/evalhub/server.py`, including nullable `sample_count`, `model_options`, result counters, failed examples, and `ok` wrappers. Implement one private generic `fetchJson<T>()` that throws `ApiError` when HTTP is unsuccessful or `body.ok === false`.

Use `URLSearchParams` for Ollama queries and explicit POST bodies for prepare and run requests. Do not let components call `fetch` directly.

- [ ] **Step 4: Verify API tests and types**

Run:

```bash
npm --prefix frontend run test:run -- src/lib/api.test.ts
npm --prefix frontend run typecheck
```

Expected: PASS and exit 0.

- [ ] **Step 5: Commit the API boundary**

```bash
git add frontend/src/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat: add typed EvalHub API client"
```

---

### Task 3: Evaluation Form Rules

**Files:**
- Create: `frontend/src/lib/evaluation.ts`
- Create: `frontend/src/lib/evaluation.test.ts`

**Interfaces:**
- Consumes: `EvaluationRequest` from `src/types.ts`
- Produces: `EvaluationFormValues`
- Produces: `buildEvaluationRequest(values): EvaluationRequest`
- Produces: `validateEvaluation(values): Record<string, string>`
- Produces: `formatScore(value): string`, `formatPassRate(passed, total): string`

- [ ] **Step 1: Write failing payload and validation tests**

Cover all three sample modes and contextual validation:

```ts
expect(buildEvaluationRequest({ ...baseValues, sampleMode: "all" })).not.toHaveProperty("limit");
expect(buildEvaluationRequest({ ...baseValues, sampleMode: "quick" })).not.toHaveProperty("limit");
expect(buildEvaluationRequest({ ...baseValues, sampleMode: "custom", limit: "20" })).toMatchObject({
  sample_mode: "custom",
  limit: 20,
});
expect(validateEvaluation({ ...baseValues, sampleMode: "custom", limit: "0" })).toEqual({
  limit: "样本数量必须是大于 0 的整数",
});
```

Also assert MMLU retains `subject`, GSM8K omits `subject`, zero totals format as `0%`, and scores render to four decimal places.

- [ ] **Step 2: Run tests and verify failure**

Run: `npm --prefix frontend run test:run -- src/lib/evaluation.test.ts`

Expected: FAIL because `evaluation.ts` does not exist.

- [ ] **Step 3: Implement pure form helpers**

Keep browser state and DOM concerns out of this module. `quick` sends `sample_mode: "quick"` and relies on the backend's fixed limit of 5. `custom` accepts only `/^[1-9]\d*$/`. `subject` is included only for MMLU.

- [ ] **Step 4: Verify helper tests**

Run:

```bash
npm --prefix frontend run test:run -- src/lib/evaluation.test.ts
npm --prefix frontend run typecheck
```

Expected: PASS and exit 0.

- [ ] **Step 5: Commit form rules**

```bash
git add frontend/src/lib/evaluation.ts frontend/src/lib/evaluation.test.ts
git commit -m "feat: define evaluation form rules"
```

---

### Task 4: Blue-White Dashboard Shell and Status Modules

**Files:**
- Create: `frontend/src/components/ui/Button.tsx`
- Create: `frontend/src/components/ui/Badge.tsx`
- Create: `frontend/src/components/ui/Panel.tsx`
- Create: `frontend/src/components/ui/FieldMessage.tsx`
- Create: `frontend/src/components/dashboard/Header.tsx`
- Create: `frontend/src/components/dashboard/MetricStrip.tsx`
- Create: `frontend/src/components/dashboard/OllamaPanel.tsx`
- Create: `frontend/src/hooks/useEvalHub.ts`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: typed functions from `src/lib/api.ts`
- Produces: `useEvalHub(model, baseUrl)` with `datasets`, `ollama`, `result`, loading flags, module errors, `refresh`, `prepare`, and `run`
- Produces: reusable UI primitives with `className`, native element props, and visible focus states

- [ ] **Step 1: Write the failing shell test**

Mock `src/lib/api.ts` with resolved health, dataset, and Ollama fixtures. Render `<App />` and assert:

```ts
expect(await screen.findByRole("heading", { name: "模型评测工作台" })).toBeInTheDocument();
expect(screen.getByText("本地环境")).toBeInTheDocument();
expect(screen.getByText("Ollama 已就绪")).toBeInTheDocument();
expect(screen.queryByText("模型注册")).not.toBeInTheDocument();
expect(screen.queryByText("排行榜")).not.toBeInTheDocument();
```

Also assert `getHealth`, `getDatasets`, and `getOllamaStatus` are all called on mount and again after the refresh button is clicked.

- [ ] **Step 2: Run the shell test and verify failure**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL because the dashboard components and hook do not exist.

- [ ] **Step 3: Implement UI primitives and status orchestration**

Build local shadcn-style primitives using semantic native elements and the `cn` helper. Use small radii, 1px borders, restrained shadows, blue focus rings, and no gradient fills.

Implement `useEvalHub` so `refresh()` uses `Promise.allSettled`; a failed Ollama request must not erase valid dataset state. Track `refreshing`, `preparingDataset`, and `runningEvaluation` separately.

- [ ] **Step 4: Compose the blue-white shell**

Build a sticky white header with a compact blue square mark, product title, local-environment badge, service badge, and refresh action. Below it, use a centered `max-width` container, metric strip, and Ollama panel. Replace the temporary `App.tsx` shell with these modules. Use Lucide icons only where they improve scanning: refresh, server, database, model, and external link.

Do not render a sidebar, hamburger menu, dead link, or placeholder product section.

- [ ] **Step 5: Verify shell tests, typecheck, and focused accessibility**

Run:

```bash
npm --prefix frontend run test:run -- src/App.test.tsx
npm --prefix frontend run typecheck
```

Expected: PASS. Every clickable control is discoverable by role and accessible name.

- [ ] **Step 6: Commit the shell**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components frontend/src/hooks/useEvalHub.ts frontend/src/styles.css
git commit -m "feat: build blue-white dashboard shell"
```

---

### Task 5: Evaluation Builder and Dataset Assets

**Files:**
- Create: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Create: `frontend/src/components/dashboard/DatasetTable.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `buildEvaluationRequest`, `validateEvaluation`, `Dataset[]`, `ModelOption[]`
- Produces: validated form submissions to `useEvalHub().run`
- Produces: dataset preparation actions to `useEvalHub().prepare`

- [ ] **Step 1: Add failing interaction tests**

Use `userEvent` to assert:

```ts
await user.selectOptions(screen.getByLabelText("数据集"), "mmlu");
expect(screen.getByLabelText("MMLU 学科")).toBeInTheDocument();
await user.click(screen.getByRole("radio", { name: "自定义" }));
expect(screen.getByLabelText("自定义样本数量")).toBeInTheDocument();
await user.clear(screen.getByLabelText("自定义样本数量"));
await user.type(screen.getByLabelText("自定义样本数量"), "0");
await user.click(screen.getByRole("button", { name: "发起评测" }));
expect(screen.getByText("样本数量必须是大于 0 的整数")).toBeInTheDocument();
expect(runEvaluation).not.toHaveBeenCalled();
```

Add assertions that GSM8K hides the subject, dataset rows expose real source links, prepared rows show `已缓存`, and the row cache button calls `prepareDataset("gsm8k")` exactly once while disabled during the request.

- [ ] **Step 2: Run interaction tests and verify failure**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL because the form and table are not rendered.

- [ ] **Step 3: Implement the evaluation builder**

Use controlled React state with defaults: GSM8K, Ollama, the first available model or `qwen2.5:0.5b`, `http://127.0.0.1:11434`, and `all` samples. Render sample modes as accessible radio cards. Show contextual fields only when applicable and field-level validation directly below the input.

Separate selection from execution: changing the model refreshes Ollama status after the user commits the select change; it must not submit the evaluation form.

- [ ] **Step 4: Implement the compact dataset table/list**

Desktop uses aligned columns for dataset, task, metric, samples, status, and action. At narrow widths each row becomes a labeled card without horizontal scrolling. Put local path and official source in a secondary detail row, not a separate navigation page.

- [ ] **Step 5: Verify interactions and build**

Run:

```bash
npm --prefix frontend run test:run -- src/App.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: PASS and production assets emitted.

- [ ] **Step 6: Commit the workflow**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/components/dashboard/DatasetTable.tsx
git commit -m "feat: add evaluation and dataset workflows"
```

---

### Task 6: Result, Loading, and Error Presentation

**Files:**
- Create: `frontend/src/components/dashboard/ResultPanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/hooks/useEvalHub.ts`

**Interfaces:**
- Consumes: `EvaluationResult | null`, evaluation loading state, and module error strings
- Produces: human-readable result summary and collapsed diagnostic JSON

- [ ] **Step 1: Add failing result and resilience tests**

Assert the empty state appears before a run. Mock a successful evaluation and assert status, benchmark, model, `passed/total`, pass rate, and four-decimal average score. Assert raw JSON is inside a `<details>` element without the `open` attribute.

Mock only `getOllamaStatus` as rejected and assert the Ollama panel shows its Chinese error while dataset rows still render. Mock `runEvaluation` as rejected and assert the error appears in the result panel, not only in raw JSON.

- [ ] **Step 2: Run tests and verify failure**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL because the result panel and isolated error handling are incomplete.

- [ ] **Step 3: Implement result and module-local states**

Use a five-cell result summary on wide screens and a two/one-column layout at narrower widths. Show a compact empty illustration made from CSS and Lucide icons, not a remote image. Use `aria-live="polite"` for request progress and errors. Preserve the last successful result while a refresh is running.

- [ ] **Step 4: Verify the complete React behavior suite**

Run:

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all frontend tests PASS; typecheck and build exit 0.

- [ ] **Step 5: Commit result states**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components/dashboard/ResultPanel.tsx frontend/src/hooks/useEvalHub.ts
git commit -m "feat: present evaluation results and errors"
```

---

### Task 7: Production Static Hosting and Local Documentation

**Files:**
- Modify: `src/evalhub/server.py`
- Create: `tests/test_server_frontend.py`
- Modify: `scripts/start_local.sh`
- Modify: `README.md`
- Modify: `docs/getting-started/LOCAL_RUN.md`

**Interfaces:**
- Produces: `frontend_directory(project_root: Path) -> Path`
- Consumes: `frontend/dist/index.html`
- Preserves: all existing API routes under `/api/*`

- [ ] **Step 1: Write the failing static-root test**

Create `tests/test_server_frontend.py`:

```py
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evalhub.server import frontend_directory


class FrontendDirectoryTests(unittest.TestCase):
    def test_uses_vite_dist_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = root / "frontend" / "dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")

            self.assertEqual(frontend_directory(root), dist)

    def test_requires_a_built_frontend(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "npm --prefix frontend run build"):
                frontend_directory(Path(temp_dir))
```

- [ ] **Step 2: Run the Python test and verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_server_frontend -v`

Expected: FAIL because `frontend_directory` does not exist.

- [ ] **Step 3: Implement Vite build serving**

Add a pure `frontend_directory(project_root)` helper that requires `frontend/dist/index.html`. Update `EvalHubRequestHandler.__init__` to call it when no explicit directory is passed. Keep `/` mapped to `/index.html`; do not add SPA fallback for arbitrary fake routes because the app has no router.

- [ ] **Step 4: Update startup behavior and documentation**

Before starting Python, `scripts/start_local.sh` must check for `frontend/node_modules`. If dependencies are absent, print `npm --prefix frontend install` and exit nonzero. When dependencies exist, always run `npm --prefix frontend run build` before starting Python so `frontend/dist` cannot be stale. Preserve the existing Ollama lifecycle and cleanup behavior.

Document two workflows:

```bash
# Production-like single server
npm --prefix frontend install
npm --prefix frontend run build
./scripts/start_local.sh

# Development with hot reload (two terminals)
./scripts/start_local.sh
npm --prefix frontend run dev
```

The Vite URL is `http://127.0.0.1:5173`; API traffic proxies to `http://127.0.0.1:8000`.

- [ ] **Step 5: Run full automated verification**

Run:

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
PYTHONPATH=src .venv/bin/python -m unittest discover tests
.venv/bin/python -m compileall -q src run_evalhub.py tests
```

Expected: all tests PASS, typecheck/build exit 0, and Python compilation exits 0.

- [ ] **Step 6: Commit integration and docs**

```bash
git add src/evalhub/server.py tests/test_server_frontend.py scripts/start_local.sh README.md docs/getting-started/LOCAL_RUN.md
git commit -m "feat: serve the React console locally"
```

---

### Task 8: Browser Visual and Interaction Verification

**Files:**
- Modify if defects are found: `frontend/src/**/*.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: built frontend at `http://127.0.0.1:8000`
- Produces: verified desktop and narrow-screen behavior with no dead navigation

- [ ] **Step 1: Start the application and inspect the initial screen**

Run `./scripts/start_local.sh`, open `http://127.0.0.1:8000`, and confirm:

- Blue-white palette and restrained Vercel-like density are visually consistent.
- There is no sidebar and no placeholder navigation.
- Header, metrics, Ollama status, evaluation builder, dataset assets, and results have a clear reading order.
- Loading and error states do not cause layout jumps or horizontal overflow.

- [ ] **Step 2: Verify real interactions**

Exercise refresh, GSM8K/MMLU switching, MMLU subject visibility, all/quick/custom sample modes, invalid custom count, model switching, JSON disclosure, and dataset preparation. Submit a safe Oracle quick evaluation when available so the result layout can be verified without depending on an Ollama model response.

- [ ] **Step 3: Verify responsive behavior**

Inspect at approximately 1440px, 1024px, 768px, and 390px viewport widths. Confirm there is no horizontal scrollbar, table rows remain understandable on mobile, controls remain reachable by keyboard, and focus rings are visible.

- [ ] **Step 4: Fix discovered defects and rerun focused checks**

For each defect, first add or refine the closest automated test when behavior is testable. Apply the smallest CSS/component fix, then rerun `npm --prefix frontend run test:run`, `typecheck`, and `build`.

- [ ] **Step 5: Commit visual verification fixes if needed**

```bash
git add frontend/src
git commit -m "fix: polish responsive console interactions"
```

If no files changed, do not create an empty commit.
