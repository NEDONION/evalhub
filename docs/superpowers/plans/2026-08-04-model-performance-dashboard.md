# 模型历史成绩与任务类型分区 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 区分模型与 Agent 评测任务，并新增按相同 Benchmark/Suite 比较模型最佳成绩和历史趋势的页面。

**Architecture:** SQLite 继续作为唯一事实来源；仓储读取轻量成绩摘要，纯聚合模块计算范围、模型排名和纪录点，HTTP 提供只读聚合 API。前端任务中心只增加本地类型筛选，模型成绩页消费聚合 API 并用原生 SVG/CSS 绘图。

**Tech Stack:** Python 3.11、SQLite、原生 `http.server`、pytest、React 19、TypeScript、Tailwind CSS、Vitest；不增加第三方依赖。

## Global Constraints

- 只比较同一 `benchmark:<dataset>` 或 `suite:<suite_id>`，Agent 任务不进入模型榜单。
- 模型排名使用历史最高 `average_score`，严格超过此前最好成绩才算刷新纪录。
- Python 新增或修改的方法必须提供详细中文 docstring，并满足仓库中文注释密度规则。
- 前端保持原生 React/TypeScript 与现有视觉系统，不引入图表库。
- 先运行相关测试，再运行完整 pytest、Ruff、前端测试、类型检查、构建和 `git diff --check`。

---

### Task 1: SQLite 成绩聚合

**Files:**
- Create: `src/evalhub/tasks/performance.py`
- Modify: `src/evalhub/tasks/repository.py`
- Modify: `src/evalhub/tasks/service.py`
- Test: `tests/test_model_performance.py`

**Interfaces:**
- Consumes: `EvaluationTask` 的 `request.evaluation_type`、`request.dataset`、`request.suite_id`、`model`、`benchmark`、`average_score` 和终态时间。
- Produces: `build_model_performance(tasks: list[EvaluationTask], scope: str | None) -> ModelPerformanceReport`；`TaskService.model_performance(scope: str | None) -> ModelPerformanceReport`。

- [ ] **Step 1: 写入范围隔离与纪录判断失败测试**

```python
report = build_model_performance(
    [model_task("qwen", "gsm8k", 0.6), model_task("qwen", "gsm8k", 0.8)],
    "benchmark:gsm8k",
)
assert report.models[0].best_score == 0.8
assert report.models[0].history[-1].improvement == 0.2
assert all(item.scope_key == "benchmark:gsm8k" for item in report.models[0].history)
```

- [ ] **Step 2: 运行测试并确认因聚合接口不存在而失败**

Run: `.venv/bin/python -m pytest tests/test_model_performance.py -q`

- [ ] **Step 3: 实现纯聚合模型和轻量仓储查询**

`performance.py` 定义不可变 dataclass：`PerformanceScope`、`PerformancePoint`、`ModelPerformance`、`ModelPerformanceReport`。仓储新增 `list_scored()`，SQL 只读取 `_SUMMARY_COLUMNS` 且限制 `average_score IS NOT NULL`。聚合逻辑过滤 Agent，按稳定范围分组，按时间计算纪录，再按最佳分、最新分、模型名排序。

- [ ] **Step 4: 运行聚合与服务测试**

Run: `.venv/bin/python -m pytest tests/test_model_performance.py tests/test_task_service.py -q`

### Task 2: 模型成绩只读 API

**Files:**
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_task_api.py`
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/architecture/20260804_数据模型.md`

**Interfaces:**
- Consumes: `TaskService.model_performance(scope)`。
- Produces: `GET /api/model-performance?scope=benchmark:gsm8k`，响应字段为 `scopes`、`selected_scope`、`models`、`record`。

- [ ] **Step 1: 添加默认范围、指定范围和未知范围 API 失败测试**

```python
response = handler_get("/api/model-performance?scope=benchmark:gsm8k")
assert response.status == 200
assert response.json["selected_scope"]["key"] == "benchmark:gsm8k"
```

- [ ] **Step 2: 运行 API 测试并确认 404/缺少路由失败**

Run: `.venv/bin/python -m pytest tests/test_task_api.py -q`

- [ ] **Step 3: 实现序列化和 GET 路由**

`model_performance_report()` 将 dataclass 转换为 JSON 兼容字典；路由读取单个 `scope` 查询参数，未知范围转换为 `400`，空历史返回 `200` 和空集合。

- [ ] **Step 4: 更新 API 与数据口径文档并复跑测试**

Run: `.venv/bin/python -m pytest tests/test_model_performance.py tests/test_task_api.py -q`

### Task 3: 任务中心类型筛选

**Files:**
- Modify: `frontend/src/components/dashboard/EvaluationTaskPanel.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `EvaluationTaskSummary.evaluation_type`。
- Produces: “全部 / 模型评测 / Agent 评测”筛选、类型计数、任务行类型标签和分组空态。

- [ ] **Step 1: 添加筛选和类型标签失败测试**

```tsx
await user.click(screen.getByRole("button", { name: "Agent 评测 1" }));
expect(screen.getByRole("button", { name: /查看任务 agent-task/ })).toBeVisible();
expect(screen.queryByRole("button", { name: /查看任务 model-task/ })).not.toBeInTheDocument();
```

- [ ] **Step 2: 运行 App 测试并确认筛选控件不存在**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

- [ ] **Step 3: 实现本地筛选、切换选中任务和类型标签**

筛选器使用三个等宽按钮；点击时立即调用 `onSelect` 选择该分组首条任务。`evaluation_type` 缺省按 `model` 兼容旧数据。任务标题旁使用蓝色“模型”或琥珀色“Agent”小标签。

- [ ] **Step 4: 复跑 App 测试**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

### Task 4: 模型成绩页面与对比图

**Files:**
- Create: `frontend/src/components/dashboard/ModelPerformancePanel.tsx`
- Create: `frontend/src/components/dashboard/ModelPerformancePanel.test.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/hooks/useEvalHub.ts`
- Modify: `frontend/src/hooks/useEvalHub.test.ts`
- Modify: `frontend/src/components/dashboard/SidebarNav.tsx`
- Modify: `frontend/src/components/dashboard/OverviewPanel.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `getModelPerformance(scope?: string)` 返回的 `ModelPerformanceResponse`。
- Produces: 新 `WorkspaceView="performance"`、范围选择器、摘要轨道、排行榜、模型选择和 SVG 历史趋势图。

- [ ] **Step 1: 添加 API 类型和页面失败测试**

```tsx
expect(screen.getByRole("heading", { name: "模型历史成绩" })).toBeVisible();
expect(screen.getByRole("img", { name: "qwen2.5:0.5b 历史成绩趋势" })).toBeVisible();
expect(screen.getByText("刷新纪录")).toBeVisible();
```

- [ ] **Step 2: 运行相关前端测试并确认组件/API 缺失失败**

Run: `npm --prefix frontend run test:run -- src/lib/api.test.ts src/hooks/useEvalHub.test.ts src/components/dashboard/ModelPerformancePanel.test.tsx`

- [ ] **Step 3: 实现 API 客户端和 Hook 状态**

增加 `ModelPerformanceScope`、`ModelPerformancePoint`、`ModelPerformanceModel`、`ModelPerformanceResponse` 类型。Hook 初始刷新读取默认范围，范围切换读取指定范围；已完成模型任务签名变化时刷新当前成绩范围。

- [ ] **Step 4: 实现独立页面和无依赖图表**

排行榜使用 0–100% CSS 水平量尺；趋势图用 SVG `polyline` 和 `circle`，绿色圆点表示 `improvement !== null`。所有图形同时输出模型、成绩和时间文本，空态提供进入发起评测页的按钮。

- [ ] **Step 5: 接入导航并复跑相关测试**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx src/components/dashboard/ModelPerformancePanel.test.tsx src/hooks/useEvalHub.test.ts src/lib/api.test.ts`

### Task 5: 完整验证

**Files:**
- Modify: `README.md`（如工作区导航说明已有对应章节）

**Interfaces:**
- Consumes: Tasks 1–4 的全部行为。
- Produces: 可发布且文档一致的功能。

- [ ] **Step 1: 运行 Python 静态与完整测试**

Run: `.venv/bin/python -m ruff check .`

Run: `.venv/bin/python -m pytest`

- [ ] **Step 2: 运行前端完整检查**

Run: `npm --prefix frontend run test:run`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

- [ ] **Step 3: 检查最终 diff**

Run: `git diff --check`

确认只包含模型历史成绩、任务类型区分、对应测试和文档，不覆盖工作区中其他 Agent 的修改。
