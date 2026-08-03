# Codex Agent 评测与六边形报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 EvalHub 任务中心和 React 控制台中实现真实 Codex Agent 评测，用户可以发起任务、查看执行状态和结果，并获得六边形能力报告。

**Architecture:** 复用现有 `EvaluationTaskService`、SQLite、子进程执行器和轮询 UI。任务请求通过 `evaluation_type` 分派：模型评测继续调用现有 Runner，Agent 评测调用 Codex CLI 原生 Ollama Provider，在临时 Git 工作区执行 Coding Mini 样本并由隐藏 Verifier 评分。前端使用原生 SVG 展示六维能力，不新增图表或 Agent 框架依赖。

**Tech Stack:** Python 3.11、标准库 subprocess/tempfile/pathlib、Codex CLI、Ollama、pytest、React 19、TypeScript、Tailwind CSS、Vitest、Testing Library、原生 SVG。

## Global Constraints

- 保持现有模型评测、同步 `/api/evaluations/run`、CLI、GSM8K 和 MMLU 行为兼容。
- Agent MVP 只支持 `codex + ollama + coding_mini`，不提前实现 Responses Bridge、Docker、Agent Registry、Artifact Store 或 SWE-bench。
- 真实 Agent 样本只能写入新建临时工作区，并使用 Codex `workspace-write` sandbox。
- 单元测试不得调用真实 Codex、Ollama、网络或公开数据集。
- Python 新增或修改函数必须有详细中文 docstring，并按仓库规则添加中文逻辑注释。
- TypeScript/TSX 新增或修改函数和回调必须有详细中文注释，并满足连续五行代码注释规则。
- 不新增 Python 或前端第三方依赖。
- 本任务开始前已有任务中心和前端未提交修改；不得覆盖、回退或擅自提交这些既有修改。

---

### Task 1: Agent 请求与 API 契约

**Files:**
- Modify: `src/evalhub/tasks/models.py`
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_task_api.py`
- Modify: `tests/test_task_repository.py`

**Interfaces:**
- Produces: `EvaluationType = Literal["model", "agent"]`。
- Extends: `TaskRequest.evaluation_type: EvaluationType = "model"`。
- Extends: `TaskRequest.agent_framework: str | None = None`。
- API: Agent 请求只允许 `dataset="coding_mini"`、`adapter="ollama"`、`agent_framework="codex"`。

- [ ] **Step 1: 写失败的 Agent 请求 API 测试**

```python
def test_create_agent_evaluation_returns_accepted_task() -> None:
    service = FakeTaskService(task_fixture())
    status, response = call_handler(
        method="POST",
        path="/api/evaluations",
        service=service,
        payload={
            "evaluation_type": "agent",
            "agent_framework": "codex",
            "dataset": "coding_mini",
            "adapter": "ollama",
            "model": "qwen2.5-coder:7b",
            "base_url": "http://127.0.0.1:11434",
            "sample_mode": "quick",
        },
    )
    assert status == 202
    assert service.submitted_request is not None
    assert service.submitted_request.evaluation_type == "agent"
    assert service.submitted_request.agent_framework == "codex"
```

同时增加错误请求测试：未知 `evaluation_type`、Agent 使用非 `coding_mini`、未知框架、非 Ollama Adapter。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_task_repository.py -q`

Expected: FAIL，因为 `TaskRequest` 没有 Agent 字段，API 不验证 Agent 组合。

- [ ] **Step 3: 最小实现请求字段和验证**

```python
EvaluationType = Literal["model", "agent"]

@dataclass(frozen=True)
class TaskRequest:
    dataset: str
    adapter: str
    model: str
    base_url: str
    sample_mode: str
    subject: str
    limit: int | None
    evaluation_type: EvaluationType = "model"
    agent_framework: str | None = None
```

`_task_request()` 对旧请求默认 `model`；Agent 分支按固定组合返回清晰 `ValueError`。`task_summary()` 增加 `evaluation_type` 与 `agent_framework`。

- [ ] **Step 4: 验证 GREEN 和兼容性**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_task_repository.py -q`

Expected: PASS，旧 JSON 请求和 SQLite 记录仍可依赖 dataclass 默认值恢复。

### Task 2: Codex CLI Runner

**Files:**
- Create: `src/evalhub/agent/__init__.py`
- Create: `src/evalhub/agent/codex.py`
- Create: `tests/test_codex_agent.py`

**Interfaces:**
- Produces: `CodexRunResult`。
- Produces: `CodexAgentRunner.run(instruction, model, base_url, workspace, timeout_seconds) -> CodexRunResult`。
- Injects: `run_command` 默认为 `subprocess.run`，测试传入 Fake，避免真实 Codex。

- [ ] **Step 1: 写失败的命令构造与结果测试**

```python
def test_codex_runner_uses_fixed_local_ollama_scaffold(tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        output = tmp_path / ".evalhub" / "final-message.txt"
        output.parent.mkdir(parents=True)
        output.write_text("done", encoding="utf-8")
        return CompletedProcess(command, 0, '{"type":"turn.completed"}\n', "")

    result = CodexAgentRunner(run_command=fake_run).run(
        instruction="Fix the bug",
        model="qwen2.5-coder:7b",
        base_url="http://127.0.0.1:11434",
        workspace=tmp_path,
        timeout_seconds=30,
    )

    assert observed["command"][:3] == ["codex", "exec", "--oss"]
    assert "--local-provider" in observed["command"]
    assert "workspace-write" in observed["command"]
    assert result.final_message == "done"
    assert result.event_count == 1
```

增加非零退出、无最终消息和 `subprocess.TimeoutExpired` 的测试，断言稳定 `CodexAgentError`。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_codex_agent.py -q`

Expected: FAIL，因为 `evalhub.agent.codex` 尚不存在。

- [ ] **Step 3: 实现最小 Runner**

```python
@dataclass(frozen=True)
class CodexRunResult:
    final_message: str
    event_count: int
    return_code: int
    wall_time_seconds: float

class CodexAgentRunner:
    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CodexRunResult:
        ...
```

命令必须包含 `--oss`、`--local-provider ollama`、`--ephemeral`、`--ignore-user-config`、`--json` 和 `--sandbox workspace-write`。环境从 `os.environ.copy()` 派生，只增加 `OLLAMA_HOST` 和临时 `CODEX_HOME`；错误正文截断到 2000 字符。

- [ ] **Step 4: 验证 GREEN**

Run: `.venv/bin/python -m pytest tests/test_codex_agent.py -q`

Expected: PASS，测试未启动真实 Codex。

### Task 3: Coding Mini Benchmark 与六维聚合

**Files:**
- Create: `src/evalhub/benchmarks/__init__.py`
- Create: `src/evalhub/benchmarks/coding_mini.py`
- Create: `tests/test_coding_mini.py`

**Interfaces:**
- Produces: `CAPABILITY_DIMENSIONS`，固定 6 个 key 和中文 label。
- Produces: `coding_mini_samples() -> tuple[CodingAgentSample, ...]`。
- Produces: `run_codex_agent_benchmark(...) -> dict[str, object]`。
- Consumes: 任何实现 `run(...) -> CodexRunResult` 的 Runner 实例；生产默认 `CodexAgentRunner`。

- [ ] **Step 1: 写失败的工作区、Verifier 和聚合测试**

```python
def test_coding_mini_uses_hidden_verifier_and_builds_six_dimensions(tmp_path: Path) -> None:
    runner = PassingFakeRunner()
    progress: list[tuple[int, int]] = []
    result = run_codex_agent_benchmark(
        job_id="job_agent",
        model="local-test",
        base_url="http://127.0.0.1:11434",
        limit=3,
        on_progress=lambda completed, total: progress.append((completed, total)),
        runner=runner,
        runtime_root=tmp_path,
    )

    assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert result["passed_samples"] == 3
    assert len(result["capability_report"]["dimensions"]) == 6
    assert all(item["score"] == 1.0 for item in result["capability_report"]["dimensions"])
```

`PassingFakeRunner` 根据 instruction 修改真实临时文件；Verifier 必须执行真实断言，而不是相信 Fake 的返回值。增加一条失败样本测试，手工断言维度分数和失败摘要。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_coding_mini.py -q`

Expected: FAIL，因为 Benchmark 模块不存在。

- [ ] **Step 3: 实现 6 条样本与隐藏 Verifier**

```python
@dataclass(frozen=True)
class CodingAgentSample:
    id: str
    instruction: str
    files: dict[str, str]
    verifier_code: str
    capability_weights: dict[str, float]
```

每条样本写入 `.runtime/agent-runs/<job>/<sample>/workspace`，执行 `git init` 和初始提交。Verifier 在 Agent 退出后以 `sys.executable -c verifier_code` 运行，`verifier_code` 不写入工作区。能力分数按 `sum(passed * weight) / sum(weight)` 聚合。

- [ ] **Step 4: 实现结果契约和样本错误隔离**

返回字段必须包含普通结果公共字段，以及：

```python
{
    "evaluation_type": "agent",
    "agent": {"framework": "codex", "cli_version": ..., "scaffold_hash": ...},
    "capability_report": {"overall_score": ..., "dimensions": [...]},
    "sample_results": [...],
}
```

Codex 超时或非零退出只让当前样本失败；Verifier 自身无法执行则抛出任务级异常。

- [ ] **Step 5: 验证 GREEN**

Run: `.venv/bin/python -m pytest tests/test_coding_mini.py -q`

Expected: PASS，六维顺序固定且 quick 3 条覆盖全部维度。

### Task 4: 任务执行分派与 API 结果

**Files:**
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `tests/test_task_executor.py`
- Modify: `tests/test_task_api.py`

**Interfaces:**
- Model request: 继续调用 `run_real_benchmark(...)`。
- Agent request: 调用 `run_codex_agent_benchmark(...)`，quick 映射为 3 条。
- Progress、资源、取消和 SQLite 终态继续使用现有任务中心。

- [ ] **Step 1: 写失败的 Agent 分派测试**

```python
def test_evaluation_process_dispatches_agent_request(monkeypatch: pytest.MonkeyPatch) -> None:
    request = replace(
        request_fixture(),
        evaluation_type="agent",
        agent_framework="codex",
        dataset="coding_mini",
    )
    events = RecordingQueue()
    observed: dict[str, object] = {}

    def fake_agent_benchmark(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return agent_result_fixture()

    monkeypatch.setattr(executor_module, "run_codex_agent_benchmark", fake_agent_benchmark)
    _evaluation_process("job_agent", asdict(request), events)
    assert observed["limit"] == 3
    assert events.events[-1]["result"]["evaluation_type"] == "agent"
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_task_executor.py tests/test_task_api.py -q`

Expected: FAIL，因为执行器总是调用普通 Benchmark。

- [ ] **Step 3: 实现单一分派分支**

在 `_evaluation_process()` 解析 limit 后只增加一个清晰分支：

```python
if request.evaluation_type == "agent":
    result = run_codex_agent_benchmark(...)
else:
    result = run_real_benchmark(...)
```

Agent quick 使用 3，model quick 保持 5。结果摘要继续从公共 `benchmark`、`total_samples`、`passed_samples` 和 `average_score` 字段读取。

- [ ] **Step 4: 验证任务中心回归**

Run: `.venv/bin/python -m pytest tests/test_task_executor.py tests/test_task_api.py tests/test_task_repository.py tests/test_task_service.py -q`

Expected: PASS。

### Task 5: 前端 Agent 评测请求与表单

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/evaluation.ts`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `EvaluationType = "model" | "agent"`。
- Extends: `DatasetName` with `coding_mini`，`EvaluationRequest` with `evaluation_type` and `agent_framework`。
- Agent form produces the fixed combination documented in Task 1。

- [ ] **Step 1: 写失败的 UI 请求测试**

```typescript
it("submits a Codex Agent evaluation from the UI", async () => {
  const user = userEvent.setup();
  vi.mocked(createEvaluation).mockResolvedValue(agentPendingTask);
  render(<App />);

  await user.click(await screen.findByRole("radio", { name: "Agent 评测" }));
  expect(screen.getByText("EvalHub Coding Mini")).toBeInTheDocument();
  expect(screen.getByText("Codex CLI")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "发起 Agent 评测" }));

  expect(createEvaluation).toHaveBeenCalledWith(
    expect.objectContaining({
      evaluation_type: "agent",
      agent_framework: "codex",
      dataset: "coding_mini",
      adapter: "ollama",
    }),
  );
});
```

增加切回模型评测后仍显示 GSM8K/MMLU 和原按钮的测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx src/lib/api.test.ts`

Expected: FAIL，因为表单没有模式选择和 Agent 请求字段。

- [ ] **Step 3: 实现类型和请求构造**

`EvaluationFormValues` 增加 `evaluationType`。`buildEvaluationRequest()` 在 Agent 分支固定输出：

```typescript
{
  evaluation_type: "agent",
  agent_framework: "codex",
  dataset: "coding_mini",
  adapter: "ollama",
  model: values.model,
  base_url: values.baseUrl,
  sample_mode: values.sampleMode,
}
```

模型分支保留现有 dataset、subject 和 adapter 行为。

- [ ] **Step 4: 实现表单模式 UI**

在表单标题下使用原生 radio 提供“模型评测 / Agent 评测”。Agent 模式用只读信息块显示 Coding Mini 和 Codex CLI，不伪装成可选择下拉框；缓存数据集按钮只在模型模式显示。

- [ ] **Step 5: 验证 GREEN**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx src/lib/api.test.ts`

Expected: PASS。

### Task 6: 六边形能力报告

**Files:**
- Create: `frontend/src/components/dashboard/AgentCapabilityHexagon.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationTaskPanel.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `AgentCapabilityHexagon({ report })`。
- Consumes: 固定六项 `AgentCapabilityReport.dimensions`。
- Existing model result: 不渲染 Agent 报告。

- [ ] **Step 1: 写失败的可访问能力报告测试**

```typescript
it("shows the six-dimension Agent capability report", async () => {
  vi.mocked(getEvaluationTasks).mockResolvedValue([agentSuccessTask]);
  vi.mocked(getEvaluationTask).mockResolvedValue(agentSuccessDetail);
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Agent 能力报告" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Agent 六维能力图" })).toBeInTheDocument();
  for (const label of ["规划", "代码理解", "实现正确性", "工具使用", "验证能力", "稳健性"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});
```

增加普通模型结果不显示图表、任务行显示“Codex Agent”标签的测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `npm --prefix frontend run test:run -- src/App.test.tsx`

Expected: FAIL，因为 Agent 类型和 Hexagon 组件不存在。

- [ ] **Step 3: 实现无依赖 SVG 几何**

使用 200×200 `viewBox`、中心 `(100, 100)`、外半径 `72`。六个顶点从顶部开始每 60° 排列；能力多边形按 `score` 缩放半径。SVG 包含 `<title>`、`<desc>`、三层网格和得分区域，不使用动画或渐变。

- [ ] **Step 4: 接入结果详情与任务标签**

Agent 成功结果在公共指标下渲染六边形和六项文本分数；普通结果继续使用原布局。任务行根据 `evaluation_type` 显示“Codex Agent”或“模型评测”。

- [ ] **Step 5: 验证前端完整质量门禁**

Run:

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: 全部 PASS。

### Task 7: 文档、真实本地集成与完成审计

**Files:**
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/architecture/20260804_系统架构.md`
- Modify: `docs/architecture/20260804_数据模型.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `frontend/README.md`

**Interfaces:**
- Documents: Agent 请求、结果、运行前提、安全边界和六维字段。
- Verifies: UI 发起、状态轮询、结果详情和真实 Codex 样本。

- [ ] **Step 1: 更新已实现行为文档**

文档只描述实际落地的本地 Codex + Ollama + Coding Mini 能力，明确不是 Docker 多租户隔离，也不宣称支持 SWE-bench。

- [ ] **Step 2: 运行完整自动化检查**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

Expected: 全部 PASS；需要回环端口的 Python 测试如受沙箱限制，在获批边界外重跑同一命令。

- [ ] **Step 3: 运行真实 Codex + Ollama 单样本**

前置检查：

```bash
codex --version
curl -s http://127.0.0.1:11434/api/tags
```

然后使用 Agent 执行入口限制为 1 条样本，确认 Codex 真实修改临时工作区、Verifier 给出结果，并且结果含六维数据。若本机没有兼容模型，保留自动化证据并明确列出未完成的外部集成验证，不伪造成功。

- [ ] **Step 4: 浏览器端到端验证**

启动 EvalHub 后，在浏览器中：

1. 切换到 Agent 评测。
2. 选择模型并提交。
3. 观察 pending/running 进度。
4. 打开成功或失败详情。
5. 对成功任务检查六边形、六项文本分数和移动端布局。

保存验证结论，不提交截图和运行产物。

## Plan Self-Review

- 需求覆盖：UI 发起、真实任务状态、结果详情和六边形报告分别由 Task 5、Task 4、Task 6 覆盖；真实 Codex 由 Task 2、Task 3 和 Task 7 覆盖。
- YAGNI：首版没有 Bridge、Docker、通用 Runner Registry、缓存、图表依赖或 SWE-bench。
- 类型一致：后端 `evaluation_type/agent_framework` 与前端同名；结果公共字段继续满足现有 SQLite 摘要读取。
- 工作区保护：计划不包含 Git commit 步骤，因为相关任务中心文件在本任务前已存在未提交修改；执行时只做精确文件编辑和验证。
