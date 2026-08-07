# Complete Agent Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 EvalHub 可以选择 MiniClaw 作为完整 Agent 运行 Coding Mini，并在任务结果中展示同一套六维能力图。

**Architecture:** 保留现有 Coding Mini 工作区和隐藏评分，只把固定 Pi Runner 替换为受控 Agent Registry。Pi 继续由 EvalHub 绑定模型；MiniClaw 通过自己的 Python 3.12+ 虚拟环境运行 EvalHub 无头桥，并加载自身配置、凭据、工具和记忆。

**Tech Stack:** Python 3.11、标准库 subprocess/JSONL、pytest、FastAPI-less local HTTP server、React 19、TypeScript、Vitest。

## Global Constraints

- EvalHub Python 最低版本为 3.11；MiniClaw 必须在自身 Python 3.12+ 进程中运行。
- 不增加第三方依赖，不修改 MiniClaw 仓库，不接受用户输入任意 shell 命令。
- MiniClaw 明文凭据只能在其子进程内读取，不能进入 EvalHub 请求、日志、Trace 或结果。
- Coding Mini 样本、隐藏 Verifier、难度和六维评分公式保持不变。
- 新增或修改的 Python 函数与方法必须有详细中文 docstring，并遵守仓库中文注释密度规则。
- 新增或修改的 TypeScript/TSX 命名函数与组件必须有详细中文 JSDoc。
- 每项行为变更先写失败测试并确认预期失败，再写最小实现。

---

### Task 1: 通用 Agent 契约与受控 Registry

**Files:**
- Create: `src/evalhub/agent/base.py`
- Create: `src/evalhub/agent/registry.py`
- Modify: `src/evalhub/agent/pi.py`
- Modify: `src/evalhub/agent/__init__.py`
- Test: `tests/test_agent_registry.py`

**Interfaces:**
- Produces: `AgentRunError`, `AgentRunResult`, `AgentTraceEvent`, `AgentMetadata`, `AgentRunner`, `AgentDefinition`, `AgentStatus`。
- Produces: `agent_definitions() -> tuple[AgentDefinition, ...]`、`agent_statuses() -> tuple[AgentStatus, ...]`、`create_agent_runner(agent_id: str, **kwargs: object) -> AgentRunner`。
- Compatibility: `PiAgentError` 与 `PiRunResult` 保留为通用类型的兼容别名。

- [ ] **Step 1: 写 Registry 失败测试**

```python
def test_agent_registry_exposes_pi_and_miniclaw_in_stable_order() -> None:
    assert [item.id for item in agent_definitions()] == ["pi", "miniclaw"]
    assert [item.model_mode for item in agent_definitions()] == ["evalhub", "agent"]


def test_agent_registry_rejects_unknown_agent() -> None:
    with pytest.raises(ValueError, match="unknown agent: missing"):
        create_agent_runner("missing")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_agent_registry.py -q`  
Expected: FAIL，原因是 `evalhub.agent.registry` 尚不存在。

- [ ] **Step 3: 实现最小公共类型**

```python
@dataclass(frozen=True)
class AgentRunResult:
    final_message: str
    event_count: int
    return_code: int
    wall_time_seconds: float
    version: str
    tool_call_count: int = 0


@dataclass(frozen=True)
class AgentMetadata:
    framework: str
    name: str
    version: str
    model: str | None
    runtime_fingerprint: str | None = None


class AgentRunner(Protocol):
    def metadata(self) -> AgentMetadata: ...
    def run(self, *, instruction: str, workspace: Path,
            timeout_seconds: float, on_event: TraceCallback | None = None) -> AgentRunResult: ...
```

`PiAgentRunner` 的公开行为保持不变；Registry 使用一个绑定 model/base_url 的轻量 Pi Runner 适配现有调用，不复制 Pi 执行逻辑。

- [ ] **Step 4: 实现静态 Registry**

```python
_DEFINITIONS = (
    AgentDefinition("pi", "Pi CLI", "由 EvalHub 选择模型的编码 Agent", "evalhub"),
    AgentDefinition("miniclaw", "MiniClaw", "使用自身配置的完整 Agent", "agent"),
)
```

Registry 只分派 `pi` 和 `miniclaw`；MiniClaw 具体类延迟导入，避免循环依赖和导入时文件访问。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `.venv/bin/python -m pytest tests/test_agent_registry.py tests/test_pi_agent.py -q`  
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/evalhub/agent/base.py src/evalhub/agent/registry.py src/evalhub/agent/pi.py src/evalhub/agent/__init__.py tests/test_agent_registry.py
git commit -m "feat: add complete agent registry"
```

### Task 2: MiniClaw 无头桥与流式 Runner

**Files:**
- Create: `src/evalhub/agent/miniclaw.py`
- Create: `src/evalhub/agent/miniclaw_bridge.py`
- Modify: `src/evalhub/agent/registry.py`
- Test: `tests/test_miniclaw_agent.py`

**Interfaces:**
- Consumes: Task 1 的 `AgentMetadata`、`AgentRunError`、`AgentRunResult`、`AgentTraceEvent`。
- Produces: `MiniClawAgentRunner(root: Path | None = None, ...)`。
- Produces: `resolve_miniclaw_root(environ: Mapping[str, str] | None = None) -> Path`。
- Bridge commands: `miniclaw_bridge.py describe` 与 `miniclaw_bridge.py run --workspace <path> --conversation-id <id>`；run 的 JSON 请求从 stdin 读取。

- [ ] **Step 1: 写路径、describe 与运行协议失败测试**

```python
def test_miniclaw_root_defaults_to_evalhub_sibling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVALHUB_MINICLAW_ROOT", raising=False)
    assert resolve_miniclaw_root().name == "miniclaw"


def test_miniclaw_runner_streams_normalized_events_and_result(tmp_path: Path) -> None:
    process = FakeStreamingProcess([
        '{"type":"event","event":{"event_type":"tool_started","actor":"miniclaw","message":null,"payload":{"tool_name":"read_file"}}}\n',
        '{"type":"result","final_message":"done","tool_call_count":1,"version":"0.1.0","model":"agent-model"}\n',
    ])
    runner = MiniClawAgentRunner(root=fake_project(tmp_path), process_factory=lambda *a, **k: process)
    events: list[AgentTraceEvent] = []
    result = runner.run(instruction="repair", workspace=tmp_path, timeout_seconds=5, on_event=events.append)
    assert result.final_message == "done"
    assert events[0]["actor"] == "miniclaw"
```

同时增加无效 JSON、多个终态、终态缺失、非零退出、超时回收和错误消息不回显 Fake 密钥的测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_miniclaw_agent.py -q`  
Expected: FAIL，原因是 MiniClaw Runner 尚不存在。

- [ ] **Step 3: 实现固定路径和就绪探测**

Runner 验证 root、`.venv/bin/python` 和桥脚本路径；启动命令必须是列表且 `shell=False`。`describe`
使用 `subprocess.run`，只接受单个 JSON 对象并转换为：

```python
AgentStatus(
    id="miniclaw",
    name="MiniClaw",
    model_mode="agent",
    available=bool(payload["available"]),
    version=_optional_text(payload.get("version")),
    model=_optional_text(payload.get("model")),
    message=str(payload["message"]),
)
```

- [ ] **Step 4: 实现流式 run 与进程组回收**

用 `Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, start_new_session=True)`。stdin 写入：

```json
{"instruction":"公开任务说明"}
```

stdout reader 线程逐行送入 Queue；主线程按 monotonic deadline 解析事件并立即调用 `on_event`。超时先
`os.killpg(..., SIGTERM)`，两秒后仍未退出再 `SIGKILL`。只允许一个 `result` 或 `error` 终态。

- [ ] **Step 5: 实现无头桥**

桥只依赖标准库和 MiniClaw 已安装包。`describe` 加载项目 `.env`、Home 和配置，输出版本、模型和
是否存在配置指定的 API Key。`run` 使用：

```python
config = load_config(paths, overrides={"workspace": str(workspace)})
runtime = create_runtime(config, paths, api_key)
result = await runtime.service.handle(
    runtime.owner_id,
    instruction,
    conversation_id,
    on_event=relay_event,
)
```

`finally` 调用 `runtime.aclose()`。事件只保留固定字段；忽略 `model_reasoning`，截断消息和 payload
预览。异常只映射为稳定 code/message，不输出原异常文本。

- [ ] **Step 6: 运行测试与只读真实 describe**

Run: `.venv/bin/python -m pytest tests/test_miniclaw_agent.py tests/test_agent_registry.py -q`  
Expected: PASS。

Run: `/Users/nedonion/PycharmProjects/miniclaw/.venv/bin/python src/evalhub/agent/miniclaw_bridge.py describe`（cwd 为 MiniClaw 根目录）  
Expected: 单个 JSON 对象，`available=true`，且输出不含 API Key。

- [ ] **Step 7: 提交**

```bash
git add src/evalhub/agent/miniclaw.py src/evalhub/agent/miniclaw_bridge.py src/evalhub/agent/registry.py tests/test_miniclaw_agent.py
git commit -m "feat: bridge miniclaw agent runtime"
```

### Task 3: 后端 API、Coding Mini、任务执行与 Agent 排行

**Files:**
- Modify: `src/evalhub/benchmarks/coding_mini.py`
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `src/evalhub/tasks/performance.py`
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `src/evalhub/tasks/workflow.py`
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_coding_mini.py`
- Modify: `tests/test_task_executor.py`
- Modify: `tests/test_task_api.py`
- Modify: `tests/test_model_performance.py`

**Interfaces:**
- Consumes: `create_agent_runner()` 与通用 `AgentRunner`。
- Produces: `run_agent_benchmark(..., framework: str, runner: AgentRunner | None = None) -> dict[str, object]`。
- Compatibility: `run_pi_agent_benchmark` 保留为调用 `framework="pi"` 的别名。
- Produces: `GET /api/agents`。
- Produces: MiniClaw 请求归一值 `adapter="agent-managed"`、`model="miniclaw"`、`base_url=""`。

- [ ] **Step 1: 写失败测试**

```python
def test_create_miniclaw_agent_evaluation_uses_agent_managed_runtime() -> None:
    status, response = call_handler(method="POST", path="/api/evaluations", payload={
        "evaluation_type": "agent", "agent_framework": "miniclaw",
        "dataset": "coding_mini", "sample_mode": "all", "agent_difficulty": "all",
    })
    assert status == 202
    assert service.submitted_request.adapter == "agent-managed"
    assert service.submitted_request.model == "miniclaw"


def test_agent_performance_groups_complete_agent_candidates() -> None:
    report = build_model_performance([pi_task, miniclaw_task], None, evaluation_type="agent")
    assert [item.model for item in report.models] == ["MiniClaw", "Pi · qwen"]
```

增加 `/api/agents`、MiniClaw 交叉模型字段拒绝、Executor 分派、通用结果元数据和旧 Pi 兼容测试。

- [ ] **Step 2: 运行相关测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_task_executor.py tests/test_coding_mini.py tests/test_model_performance.py -q`  
Expected: 新 MiniClaw 用例 FAIL，既有用例保持通过。

- [ ] **Step 3: 泛化 Coding Mini**

把内部协议和回调命名从 Pi 改为 Agent，`active_runner` 从 Registry 获取。结果元数据使用：

```python
metadata = active_runner.metadata()
"agent": {
    "framework": metadata.framework,
    "name": metadata.name,
    "version": metadata.version,
    "model": metadata.model,
    "runtime_fingerprint": metadata.runtime_fingerprint,
    "scaffold_hash": _scaffold_hash(selected_samples),
}
```

隐藏 Verifier、分档和六维聚合不修改。

- [ ] **Step 4: 接通 Worker 和 API**

Worker 仅为 Pi 的 openai-compatible 请求解析 EvalHub Provider API Key。随后统一：

```python
runner = create_agent_runner(
    request.agent_framework or "",
    adapter=request.adapter,
    model=request.model,
    base_url=request.base_url,
    provider_id=request.provider_id,
    api_key=api_key,
)
result = run_agent_benchmark(..., framework=request.agent_framework or "", runner=runner)
```

server 在解析通用 model 字段前先识别 Agent。Pi 继续执行原校验；MiniClaw 拒绝客户端的 adapter、
model、base_url、provider_id 并写入兼容值。`GET /api/agents` 使用 Registry 状态序列化。

- [ ] **Step 5: 调整完整 Agent 排行身份**

新增单个共享 helper：Agent 任务的 group key 为 `Pi · {task.request.model}` 或 Registry 显示名；模型
任务继续使用 `task.request.model`。保留现有性能 API 字段名，避免不必要的响应迁移。

- [ ] **Step 6: 运行后端回归**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_task_executor.py tests/test_coding_mini.py tests/test_model_performance.py tests/test_pi_agent.py tests/test_miniclaw_agent.py -q`  
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/evalhub/benchmarks/coding_mini.py src/evalhub/tasks/executor.py src/evalhub/tasks/performance.py src/evalhub/tasks/presentation.py src/evalhub/tasks/workflow.py src/evalhub/server.py tests/test_coding_mini.py tests/test_task_executor.py tests/test_task_api.py tests/test_model_performance.py
git commit -m "feat: evaluate complete agents"
```

### Task 4: 前端 Agent 选择、结果元数据和六维图入口

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/lib/evaluation.ts`
- Modify: `frontend/src/lib/evaluation.test.ts`
- Modify: `frontend/src/hooks/useEvalHub.ts`
- Modify: `frontend/src/hooks/useEvalHub.test.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.tsx`
- Modify: `frontend/src/components/dashboard/ModelPerformancePanel.tsx`
- Modify: `frontend/src/components/dashboard/ModelPerformancePanel.test.tsx`

**Interfaces:**
- Produces: `AgentDefinition`、`AgentStatusResponse`、`getAgents()`。
- Produces: `AgentFramework = "pi" | "miniclaw"`。
- Changes: `EvaluationRequest` 成为模型、Pi Agent、MiniClaw Agent 可安全构造的联合类型；MiniClaw 请求不含模型字段。
- Consumes: 既有 `AgentCapabilityReport` 和六维结果组件，不新建图表实现。

- [ ] **Step 1: 写失败测试**

```typescript
it("builds a self-managed MiniClaw evaluation request", () => {
  expect(buildEvaluationRequest({ ...values, evaluationType: "agent", agentFramework: "miniclaw" }))
    .toEqual({
      evaluation_type: "agent",
      agent_framework: "miniclaw",
      dataset: "coding_mini",
      sample_mode: "all",
      agent_difficulty: "all",
    });
});
```

App 测试选择 MiniClaw 后断言“不显示 Agent 基模/Ollama 地址”、显示自管模型、提交正确正文，并在
完成任务详情中看到六项 `capability_report.dimensions` 对应的六边形组件。

- [ ] **Step 2: 运行相关前端测试并确认 RED**

Run: `npm --prefix frontend run test:run -- src/lib/evaluation.test.ts src/App.test.tsx src/components/dashboard/ModelPerformancePanel.test.tsx`  
Expected: FAIL，原因是 Agent 类型和选择器尚未支持 MiniClaw。

- [ ] **Step 3: 增加 API 类型和加载状态**

```typescript
export interface AgentDefinition {
  id: AgentFramework;
  name: string;
  description: string;
  model_mode: "evalhub" | "agent";
  available: boolean;
  version: string | null;
  model: string | null;
  message: string;
}
```

`useEvalHub` 与其他初始元数据并行调用 `getAgents()`；错误进入现有可诊断状态，不在组件内直接 fetch。

- [ ] **Step 4: 改造表单条件渲染**

Agent 模式先显示 Agent 原生 select。Pi 复用现有 ModelSelector/ProviderSettings；MiniClaw 只显示
版本、自管模型和就绪消息。提交按钮在所选 Agent 不可用时禁用，流程第 2 步使用所选名称。

- [ ] **Step 5: 复用现有六维结果展示**

`EvaluationResultDetail` 读取通用 `agent.version ?? agent.cli_version`、`agent.name` 和
`agent.model`，继续把 `capability_report.dimensions` 交给现有能力图，不复制六边形组件。排行标题改为
“Agent 历史成绩”和“候选 Agent”。

- [ ] **Step 6: 运行前端回归**

Run: `npm --prefix frontend run test:run`  
Expected: PASS。

Run: `npm --prefix frontend run typecheck`  
Expected: PASS。

Run: `npm --prefix frontend run build`  
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add frontend/src
git commit -m "feat: select and inspect complete agents"
```

### Task 5: 文档、完整验证与真实 MiniClaw 可用性

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/20260804_系统架构.md`
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/product/20260804_Agent评测路线图.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`

**Interfaces:**
- Documents: Pi 与 MiniClaw 完整 Agent 口径、`GET /api/agents`、MiniClaw 路径配置、请求示例和真实验证步骤。

- [ ] **Step 1: 更新已实现行为文档**

把“固定 Pi 外壳”改为“完整 Agent Registry”；保留历史报告的固定 Pi 实验说明。记录：

```bash
export EVALHUB_MINICLAW_ROOT=/absolute/path/to/miniclaw
./scripts/start_local.sh
```

说明 MiniClaw 自己的 `.env`/Home 必须已初始化，EvalHub 不保存其 API Key。

- [ ] **Step 2: 运行全部静态与自动化检查**

Run: `.venv/bin/python -m ruff check .`  
Expected: PASS。

Run: `.venv/bin/python -m pytest`  
Expected: PASS。

Run: `npm --prefix frontend run test:run`  
Expected: PASS。

Run: `npm --prefix frontend run typecheck`  
Expected: PASS。

Run: `npm --prefix frontend run build`  
Expected: PASS。

Run: `git diff --check`  
Expected: PASS。

- [ ] **Step 3: 真实 MiniClaw 单样本集成验证**

先运行 `GET /api/agents` 或 Registry describe，确认 MiniClaw ready。再通过真实任务 API 创建：

```json
{
  "evaluation_type": "agent",
  "agent_framework": "miniclaw",
  "dataset": "coding_mini",
  "sample_mode": "all",
  "agent_difficulty": "easy"
}
```

轮询到终态，确认 `total_samples=2`、`agent.framework="miniclaw"`、`capability_report.dimensions`
恰好六项、结果不含 API Key。若外部模型不可用，保留自动化通过证据并准确报告集成阻塞，不伪造成功。

- [ ] **Step 4: 浏览器验收六维图**

启动后端和 Vite，选择 MiniClaw 发起相同评测。任务完成后打开详情，确认 Agent 名称、版本、自管模型、
样本结果和六边形能力图均可见，浏览器控制台无 error。

- [ ] **Step 5: 提交文档**

```bash
git add README.md docs
git commit -m "docs: explain complete agent evaluation"
```
