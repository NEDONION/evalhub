# Evaluation Task Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让任务详情通过右侧 Drawer 即时打开，消除滚过完整任务列表才能看到详情的问题。

**Architecture:** `EvaluationTaskPanel` 继续持有筛选与选择交互，并用原生 `<dialog>` 包裹现有 `TaskDetail`。任务数据、轮询、取消和重试接口保持不变。

**Tech Stack:** React 19、TypeScript、Tailwind CSS、Vitest、Testing Library、原生 HTML dialog。

## Global Constraints

- 不新增依赖，不修改后端或 API。
- 新增或修改的函数提供详细中文 JSDoc。
- 桌面右侧 Drawer，移动端全屏；支持关闭按钮、遮罩和 `Esc`。

---

### Task 1: 将内联任务详情改为右侧 Drawer

**Files:**
- Modify: `frontend/src/components/dashboard/EvaluationTaskPanel.tsx`
- Test: `frontend/src/components/dashboard/EvaluationTaskPanel.test.tsx`
- Modify: `docs/getting-started/20260804_本地运行指南.md`

**Interfaces:**
- Consumes: 现有 `selectedTaskId`、`selectedTask`、`onSelect`、`onCancel`、`onRetryNode`。
- Produces: 任务行点击后打开的原生 `dialog`，不改变组件公开 Props。

- [ ] **Step 1: 写失败的 Drawer 交互测试**

```tsx
await user.click(screen.getByRole("button", { name: "查看任务 model-task" }));
expect(screen.getByRole("dialog", { name: "任务详情" })).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "关闭任务详情" }));
expect(screen.queryByRole("dialog", { name: "任务详情" })).not.toBeInTheDocument();
```

- [ ] **Step 2: 确认测试因缺少 Drawer 而失败**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/EvaluationTaskPanel.test.tsx`

Expected: FAIL，页面中不存在 `dialog`。

- [ ] **Step 3: 用原生 dialog 实现最小 Drawer**

```tsx
const dialogRef = useRef<HTMLDialogElement>(null);
const [drawerOpen, setDrawerOpen] = useState(false);

useEffect(() => {
  if (drawerOpen) dialogRef.current?.showModal();
  else dialogRef.current?.close();
}, [drawerOpen]);
```

任务行与筛选选择统一调用 `selectTaskAndOpen`，Drawer 内复用 `TaskDetail`，并删除原页面流末尾的详情。

- [ ] **Step 4: 运行相关测试和前端检查**

Run: `npm --prefix frontend run test:run -- src/components/dashboard/EvaluationTaskPanel.test.tsx src/App.test.tsx`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: 全部通过。

- [ ] **Step 5: 执行仓库验证并提交**

Run: `.venv/bin/python -m ruff check .`

Run: `.venv/bin/python -m pytest`

Run: `git diff --check`

Expected: 全部通过；显式外部集成跳过项除外。
