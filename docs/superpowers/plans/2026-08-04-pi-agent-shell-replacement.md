# Pi Agent Shell Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用项目内固定版本的 Pi CLI 完全替换 Codex Agent 壳，保留 Coding Mini 隐藏校验，并在本机 Ollama 上跑通真实工具调用和代码修改。

**Architecture:** Python Worker 为每个样本生成隔离的 Pi 配置，通过 macOS `sandbox-exec` 启动 Pi JSONL 模式并标准化工具事件。现有 Git 差异与隐藏 Verifier 继续负责评分；API、React 和结果元数据只暴露 `pi`，不保留 Codex 兼容分支。

**Tech Stack:** Python 3.11、标准库 `subprocess/json/pathlib/urllib.parse`、Pi CLI 0.74.1、Node.js 20.6+、Ollama、pytest、React 19、TypeScript、Vitest。

## Global Constraints

- Pi 依赖固定在 `agent-runtime/`，精确版本为 `@earendil-works/pi-coding-agent@0.74.1`。
- Agent 请求只接受 `pi + ollama + coding_mini`，不增加框架选择器或兼容旧 Codex 路径。
- Pi 整个进程树必须由 `/usr/bin/sandbox-exec` 启动，只写样本工作区且只连接 loopback Ollama 端口；沙箱失败时禁止降级。
- 单元测试不得启动真实 Pi、Ollama、网络或下载模型；真实集成只在最终验收执行。
- 保护当前工作区已有的无关修改，不暂存或提交任务范围外文件。
- 修改的 Python 函数必须具有详细中文 docstring，并满足仓库中文注释密度规则。

---

## File Map

- `agent-runtime/package.json`、`agent-runtime/package-lock.json`：项目内 Pi 运行时及精确依赖锁。
- `src/evalhub/agent/pi.py`：Pi 命令、沙箱策略、隔离配置、JSONL 解析和进程回收。
- `src/evalhub/agent/__init__.py`：只导出 Pi Agent 公共类型。
- `src/evalhub/ollama_pull.py`：把现有 loopback URL 校验公开给 Pi Runner 复用。
- `src/evalhub/benchmarks/coding_mini.py`：默认使用 Pi Runner，并写入 `framework=pi`。
- `src/evalhub/tasks/executor.py`：Agent 子进程分派到 Pi benchmark。
- `src/evalhub/server.py`：Agent 请求仅接受 `agent_framework=pi`。
- `tests/test_pi_agent.py`：Pi JSONL、沙箱、配置、超时和错误回归测试。
- `tests/test_coding_mini.py`、`tests/test_task_executor.py`、`tests/test_task_api.py`、`tests/test_task_service.py`：后端端到端契约改为 Pi。
- `frontend/src/types.ts`、`frontend/src/lib/evaluation.ts`、`frontend/src/components/dashboard/EvaluationForm.tsx`：前端 Pi 类型、请求和文案。
- `frontend/src/lib/evaluation.test.ts`、`frontend/src/App.test.tsx`、`frontend/src/components/dashboard/EvaluationTaskPanel.test.tsx`：前端回归测试。
- `docs/architecture/20260804_系统架构.md`、`docs/architecture/20260804_API接口草案.md`、`docs/getting-started/20260804_本地运行指南.md`、`docs/product/20260804_Agent评测路线图.md`：同步实际 Pi 运行方式。

---

### Task 1: Pi Runner Contract

**Files:**
- Move: `tests/test_codex_agent.py` → `tests/test_pi_agent.py`
- Move: `src/evalhub/agent/codex.py` → `src/evalhub/agent/pi.py`
- Modify: `src/evalhub/agent/__init__.py`
- Modify: `src/evalhub/ollama_pull.py`

**Interfaces:**
- Produces: `PiAgentError`, `PiRunResult`, `PiAgentRunner.run(...) -> PiRunResult`。
- Produces: `AgentTraceEvent` 和 `TraceCallback`，字段保持现有任务事件协议。
- Consumes: `validate_loopback_base_url(base_url: str) -> str`。

- [ ] **Step 1: 先把 Runner 测试改成 Pi 期望**

  在 `tests/test_pi_agent.py` 中让 Fake 版本调用匹配项目内 `pi --version`，Fake 执行输出权威 `message_end` 和工具事件：

  ```python
  stdout = (
      '{"type":"session","version":3,"id":"session-1"}\n'
      '{"type":"agent_start"}\n'
      '{"type":"tool_execution_start","toolCallId":"tool-1",'
      '"toolName":"edit","args":{"path":"pricing.py"}}\n'
      '{"type":"tool_execution_end","toolCallId":"tool-1",'
      '"toolName":"edit","result":{"content":[{"type":"text","text":"done"}]},'
      '"isError":false}\n'
      '{"type":"message_end","message":{"role":"assistant",'
      '"content":[{"type":"text","text":"修复完成"}]}}\n'
  )
  ```

  断言命令包含 `/usr/bin/sandbox-exec`、`--mode json`、`--no-session`、`--provider ollama`、`--model` 和固定工具；环境包含样本内 `PI_CODING_AGENT_DIR`、`PI_OFFLINE=1`、`PI_TELEMETRY=0`。断言 `models.json` 的 provider 端点是 `<base_url>/v1`，框架事件 actor 为 `pi`。

- [ ] **Step 2: 运行 Runner 测试并确认 RED**

  Run: `.venv/bin/python -m pytest tests/test_pi_agent.py -q`

  Expected: collection 或断言失败，因为 `evalhub.agent.pi`、Pi JSONL 解析和沙箱命令尚未实现。

- [ ] **Step 3: 实现最小 Pi Runner**

  `PiAgentRunner.run()` 依次执行：公开 loopback URL 校验、绝对化工作区、创建 `.evalhub/pi-home/models.json` 和 `.evalhub/tmp`、生成 Seatbelt 策略、运行 JSON 模式、解析最终消息。命令形状固定为：

  ```python
  [
      "/usr/bin/sandbox-exec", "-p", sandbox_profile, "--",
      str(pi_binary), "--mode", "json", "--no-session",
      "--provider", "ollama", "--model", model,
      "--tools", "read,write,edit,bash",
      "--no-extensions", "--no-skills", "--no-prompt-templates",
      "--no-context-files", instruction,
  ]
  ```

  Seatbelt 使用 `(allow default)`，再拒绝全部文件写和网络；仅对当前 `workspace` 的 `file-write*` 与 `localhost:<ollama-port>` 的 `network-outbound` 添加更具体允许。`message_end.message.role=assistant` 的 text content 是最终消息；`tool_execution_start/end` 映射为稳定工具事件，未知事件只计数不持久化。

- [ ] **Step 4: 运行 Runner 测试并确认 GREEN**

  Run: `.venv/bin/python -m pytest tests/test_pi_agent.py -q`

  Expected: PASS；Fake 不启动真实 Pi 或 Ollama。

- [ ] **Step 5: 暂存 Runner 变更**

  Run: `git add src/evalhub/agent/pi.py src/evalhub/agent/__init__.py src/evalhub/ollama_pull.py tests/test_pi_agent.py`

---

### Task 2: Benchmark, Worker and API Replacement

**Files:**
- Modify: `src/evalhub/benchmarks/coding_mini.py`
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_coding_mini.py`
- Modify: `tests/test_task_executor.py`
- Modify: `tests/test_task_api.py`
- Modify: `tests/test_task_service.py`

**Interfaces:**
- Consumes: `PiAgentRunner`, `PiAgentError`, `PiRunResult`。
- Produces: `run_pi_agent_benchmark(...) -> dict[str, object]`。
- Produces: Agent API 固定值 `agent_framework="pi"` 和结果 `agent.framework="pi"`。

- [ ] **Step 1: 把后端契约测试改成 Pi**

  把测试请求和结果中的 `codex` 改为 `pi`，把 benchmark 调用断言改为 `run_pi_agent_benchmark`。API 非法框架用例必须断言：

  ```python
  ({**base_payload, "agent_framework": "codex"}, "agent_framework must be pi")
  ```

  Coding Mini 测试继续用 Fake Runner，但 Fake 返回 `PiRunResult`，并断言：

  ```python
  assert result["agent"]["framework"] == "pi"
  ```

- [ ] **Step 2: 运行后端契约测试并确认 RED**

  Run: `.venv/bin/python -m pytest tests/test_coding_mini.py tests/test_task_executor.py tests/test_task_api.py tests/test_task_service.py -q`

  Expected: FAIL，因为生产入口仍导入 Codex 符号并接受 `codex`。

- [ ] **Step 3: 最小替换生产入口**

  将 `run_codex_agent_benchmark` 重命名为 `run_pi_agent_benchmark`，所有 Runner 类型、错误类型、actor、docstring 和错误分类改为 Pi。`server._task_request()` 的 Agent 分支固定：

  ```python
  agent_framework = str(payload.get("agent_framework", ""))
  if agent_framework != "pi":
      raise ValueError("agent_framework must be pi")
  ```

  `tasks/executor.py` 只导入和调用 Pi benchmark，不保留别名或回退。

- [ ] **Step 4: 运行后端契约测试并确认 GREEN**

  Run: `.venv/bin/python -m pytest tests/test_coding_mini.py tests/test_task_executor.py tests/test_task_api.py tests/test_task_service.py -q`

  Expected: PASS。

- [ ] **Step 5: 暂存后端接线变更**

  Run: `git add src/evalhub/benchmarks/coding_mini.py src/evalhub/tasks/executor.py src/evalhub/server.py tests/test_coding_mini.py tests/test_task_executor.py tests/test_task_api.py tests/test_task_service.py`

---

### Task 3: Frontend Pi Contract

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/evaluation.ts`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/lib/evaluation.test.ts`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationTaskPanel.test.tsx`

**Interfaces:**
- Produces: `AgentFramework = "pi"`。
- Produces: Agent 创建请求的 `agent_framework: "pi"`。
- Displays: `Pi CLI` 与 `macOS workspace-write 沙箱`。

- [ ] **Step 1: 修改前端测试为 Pi 期望**

  `frontend/src/lib/evaluation.test.ts` 断言 `agent_framework: "pi"`；`App.test.tsx` 的 Agent fixture、结果 metadata 和提交断言改为 Pi，并断言页面出现 `Pi CLI`、不存在 `Codex CLI`。

- [ ] **Step 2: 运行前端测试并确认 RED**

  Run: `npm --prefix frontend run test:run -- src/lib/evaluation.test.ts src/App.test.tsx src/components/dashboard/EvaluationTaskPanel.test.tsx`

  Expected: FAIL，因为类型、请求构造和表单仍固定 Codex。

- [ ] **Step 3: 最小更新前端实现**

  将 `AgentFramework` 改为 `"pi"`；Agent 请求改为 `agent_framework: "pi"`；表单壳名称改为 `Pi CLI`，说明改为 `固定工具与 macOS workspace-write 沙箱`，流程第二步改为 `Pi 使用所选基模完成任务`。

- [ ] **Step 4: 运行前端测试并确认 GREEN**

  Run: `npm --prefix frontend run test:run -- src/lib/evaluation.test.ts src/App.test.tsx src/components/dashboard/EvaluationTaskPanel.test.tsx`

  Expected: PASS。

- [ ] **Step 5: 暂存前端变更**

  Run: `git add frontend/src/types.ts frontend/src/lib/evaluation.ts frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/lib/evaluation.test.ts frontend/src/App.test.tsx frontend/src/components/dashboard/EvaluationTaskPanel.test.tsx`

---

### Task 4: Project-local Pi Runtime and Documentation

**Files:**
- Create: `agent-runtime/package.json`
- Create: `agent-runtime/package-lock.json`
- Modify: `docs/architecture/20260804_系统架构.md`
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `docs/product/20260804_Agent评测路线图.md`

**Interfaces:**
- Provides: `agent-runtime/node_modules/.bin/pi`。
- Documents: `npm --prefix agent-runtime ci --ignore-scripts` 和 `agent_framework=pi`。

- [ ] **Step 1: 创建精确依赖清单**

  `agent-runtime/package.json` 固定为：

  ```json
  {
    "name": "evalhub-agent-runtime",
    "private": true,
    "version": "0.1.0",
    "dependencies": {
      "@earendil-works/pi-coding-agent": "0.74.1"
    }
  }
  ```

- [ ] **Step 2: 安装并验证项目内 Pi**

  Run: `npm --prefix agent-runtime install --ignore-scripts`

  Run: `agent-runtime/node_modules/.bin/pi --version`

  Expected: 退出码 0，版本为 `0.74.1`；生成 `package-lock.json`，`node_modules` 继续由根 `.gitignore` 排除。

- [ ] **Step 3: 同步运行文档**

  架构文档把 Agent runner 改为 Pi JSONL + Seatbelt；API 示例固定 `"agent_framework": "pi"`；本地指南加入 Node 20.6+、项目内安装命令和 `Pi CLI` 操作；路线图把当前闭环更新为 Pi，不改写历史 superpowers 计划。

- [ ] **Step 4: 验证无生产 Codex 残留**

  Run: `rg -n "evalhub\.agent\.codex|run_codex_agent_benchmark|agent_framework.{0,8}codex|Codex CLI|Codex Agent" src tests frontend/src`

  Expected: 无运行代码和活动测试命中；路线图中作为未来外部产品示例的 Codex 不属于旧壳残留。

- [ ] **Step 5: 暂存运行时与文档**

  Run: `git add agent-runtime/package.json agent-runtime/package-lock.json docs/architecture/20260804_系统架构.md docs/architecture/20260804_API接口草案.md docs/getting-started/20260804_本地运行指南.md docs/product/20260804_Agent评测路线图.md`

---

### Task 5: Real Run, Old-data Cleanup and Full Verification

**Files:**
- Runtime delete: `.runtime/agent-runs/`
- SQLite rows: `.runtime/evalhub.db` 中 `evaluation_type='agent'` 的任务及关联数据

**Interfaces:**
- Verifies: Pi 真实 tool call、工作区变更、隐藏 Verifier 和前端 Pi 元数据。
- Preserves: 所有 `evaluation_type='model'` 任务及 Hexagon 数据。

- [ ] **Step 1: 用单条简单样本真实运行 Pi**

  通过 `.venv/bin/python` 调用 `run_pi_agent_benchmark(job_id=<new-safe-id>, model="qwen2.5-coder:7b", base_url="http://127.0.0.1:11434", difficulty="easy")`，保留真实 JSON 结果供检查。

  Expected: 至少一个样本的 `diagnostics.tool_call_count > 0`，`changed_files` 包含受控 `.py` 文件，Verifier 与文件内容一致；如果第一条样本暴露 Pi/Ollama 协议缺陷，先补失败回归测试再修实现。

- [ ] **Step 2: 精确删除旧 Agent 数据**

  在单个 SQLite 事务中先按 Agent task id 删除关联 `evaluation_node_events`、`evaluation_nodes`、`evaluation_sample_results`，再删除 `evaluation_tasks`；提交后查询确认 Agent 数为 0、model 任务数与删除前相同。随后删除精确目录 `.runtime/agent-runs`，不触碰 `.runtime/evalhub.db` 或其他运行资产。

- [ ] **Step 3: 运行相关与全量 Python 验证**

  Run: `.venv/bin/python -m pytest tests/test_pi_agent.py tests/test_coding_mini.py tests/test_task_executor.py tests/test_task_api.py tests/test_task_service.py -q`

  Run: `.venv/bin/python -m pytest`

  Run: `.venv/bin/python -m ruff check .`

  Expected: 全部退出码 0。

- [ ] **Step 4: 运行前端与仓库验证**

  Run: `npm --prefix frontend run test:run`

  Run: `npm --prefix frontend run build`

  Run: `git diff --check`

  Expected: 全部退出码 0，构建产物不进入 Git 暂存区。

- [ ] **Step 5: 审核并提交任务范围**

  用 `git diff --cached --name-only` 确认暂存区只含本计划 File Map 中的实现、测试、运行时锁文件和文档；提交信息使用 `feat: replace Codex agent shell with Pi`。不要暂存用户已有的无关修改。

---

## Self-review

- 规格覆盖：Runner、安全沙箱、Pi 精确版本、API/前端标识、旧数据删除、真实运行和完整验证分别由 Task 1–5 覆盖。
- 类型一致：生产和测试统一使用 `PiAgentRunner`、`PiRunResult`、`PiAgentError`、`run_pi_agent_benchmark`、`agent_framework="pi"`。
- 安全失败模式：URL 非 loopback、`sandbox-exec` 缺失或策略加载失败均进入明确 `PiAgentError`，没有无沙箱回退。
- 完整性扫描：每个步骤都给出了确定接口、命令和可观察结果。
