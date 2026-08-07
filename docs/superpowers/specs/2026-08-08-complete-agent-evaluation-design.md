# 完整 Agent 评测接入设计

状态：已确认，待实现  
日期：2026-08-08

## 1. 背景

当前 Agent 评测固定使用 Pi CLI，只允许替换 Ollama 或 API 基模。这个口径适合比较同一 Agent
外壳下的模型能力，但不能评测 MiniClaw 这类自行管理模型、工具、策略、记忆和运行配置的完整
Agent。

本次把评测对象提升为“完整 Agent”。Pi 和 MiniClaw 都是独立 Agent；EvalHub 只负责选择并启动
Agent、提供隔离样本工作区、收集可审计事件、执行隐藏 Verifier 和聚合结果，不介入 Agent 自身的
决策逻辑。

## 2. 目标

- 同一份 Coding Mini 可以运行 Pi 或 MiniClaw。
- Pi 保留现有由 EvalHub 选择模型和 Provider 的运行方式。
- MiniClaw 使用自己的模型、Provider、凭据、工具、策略、身份和记忆。
- 两种 Agent 复用相同工作区、Trace、隐藏评分、难度报告和六维能力报告。
- 历史 Pi 请求和结果继续可读，不迁移 SQLite 数据。
- 新 Agent 通过受控 Registry 接入，不允许页面执行任意 shell 命令。

## 3. 非目标

首版不实现动态插件发现、Python entry point、用户自定义命令、自动安装 Agent、并行样本、远程
Agent 服务、多租户沙箱或 MiniClaw 源码改造。第三个 Agent 真正接入前，不抽象通用 JSONL 插件
生态。

## 4. 方案选择

采用静态完整 Agent Registry。

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 受控 Registry + 每个 Agent 的 Runner | 采用 | 最小改动即可真实运行 Pi 和 MiniClaw，命令与路径可校验 |
| 所有 Agent 自带统一 JSONL CLI | 延后 | 长期边界清晰，但 MiniClaw 当前没有无头单任务命令 |
| 页面填写任意 shell 命令 | 不采用 | 存在命令注入、密钥泄露和不可复现风险 |

Registry 是代码内的固定映射，不增加可变配置中心。每个定义至少包含稳定 ID、显示名称、说明、
模型管理模式、就绪检查和 Runner 构造函数。首批稳定 ID 为 `pi` 和 `miniclaw`。

## 5. Agent 与 Runner 边界

`agent_framework` 保留现有字段名以兼容任务 JSON，但它的新语义是完整 Agent ID，而不是共享外壳
类型。

Coding Mini 依赖一个通用 Runner 协议：读取 Agent 描述，并在指定工作区执行一条公开任务说明。
运行结果只包含最终消息、事件数、工具调用数、退出状态、耗时和版本等外部事实。Runner 不参与
工作区生成、隐藏校验或能力评分。

Pi 现有实现继续负责 Pi JSONL、Seatbelt、Ollama 或受控 API 代理。Registry 在构造 Pi Runner 时
绑定 EvalHub 请求中的模型配置，使 Coding Mini 不再直接创建 `PiAgentRunner`。

MiniClaw 使用单独的 EvalHub Runner。由于 EvalHub 支持 Python 3.11，而 MiniClaw 要求 Python
3.12 以上，不能在 EvalHub 进程内直接导入 MiniClaw。Runner 必须启动 MiniClaw 项目自己的
`.venv/bin/python`，执行 EvalHub 提供的无头桥脚本。

## 6. MiniClaw 定位与就绪检查

MiniClaw 项目路径按以下顺序解析：

1. `EVALHUB_MINICLAW_ROOT`；
2. EvalHub 仓库同级的 `miniclaw` 目录。

实现不得包含个人绝对路径。就绪检查验证项目目录、`.venv/bin/python`、可导入的 MiniClaw 版本、
已初始化 Home、配置文件和配置指定的 API Key 是否存在。检查结果只返回版本、模型、布尔状态和稳定
诊断消息，不返回 Base URL、环境变量值、文件内容或凭据。

`GET /api/agents` 返回固定顺序的 Agent 定义：

```json
{
  "agents": [
    {
      "id": "pi",
      "name": "Pi CLI",
      "description": "由 EvalHub 选择模型的编码 Agent",
      "model_mode": "evalhub",
      "available": true,
      "version": "0.74.1",
      "model": null,
      "message": "ready"
    },
    {
      "id": "miniclaw",
      "name": "MiniClaw",
      "description": "使用自身模型、工具、策略与记忆的完整 Agent",
      "model_mode": "agent",
      "available": true,
      "version": "0.1.0",
      "model": "deepseek-v4-pro",
      "message": "ready"
    }
  ]
}
```

## 7. 请求契约

Pi 请求保持兼容：

```json
{
  "evaluation_type": "agent",
  "agent_framework": "pi",
  "dataset": "coding_mini",
  "adapter": "ollama",
  "model": "qwen2.5-coder:7b",
  "base_url": "http://127.0.0.1:11434",
  "sample_mode": "all",
  "agent_difficulty": "all"
}
```

MiniClaw 请求不接受模型或 Provider 选择：

```json
{
  "evaluation_type": "agent",
  "agent_framework": "miniclaw",
  "dataset": "coding_mini",
  "sample_mode": "all",
  "agent_difficulty": "all"
}
```

为了不迁移已有 `TaskRequest` 和 SQLite JSON，服务端把 MiniClaw 请求归一为内部兼容值：
`adapter="agent-managed"`、`model="miniclaw"`、`base_url=""`。这些值只表示任务身份，不会传给
MiniClaw，也不冒充其真实模型。客户端不能直接提交 `agent-managed` 模型适配器。

未知 Agent、不可用 Agent、Pi 缺少模型配置、MiniClaw 携带模型配置、非 Coding Mini 数据集或非
`all` 样本模式都在入队前返回明确的 400 错误。

## 8. MiniClaw 执行流

每次协议预检和正式样本都使用独立 MiniClaw 子进程：

1. EvalHub 创建并初始化 Coding Mini Git 工作区。
2. Runner 用固定参数启动 MiniClaw `.venv/bin/python` 和 EvalHub 无头桥，不经过 shell。
3. 公开任务说明通过标准输入发送，避免出现在命令行和进程列表。
4. 桥在 MiniClaw 项目目录内加载其 `.env`，解析 `MINICLAW_HOME` 或默认 Home。
5. 桥加载 MiniClaw 自身配置，只用公开 override 把 workspace 指向当前样本目录。
6. 桥使用 MiniClaw 的 `create_runtime()` 和 `TurnService.handle()` 运行唯一会话。
7. 会话 ID 包含 EvalHub Job 和样本 ID，避免不同样本复用聊天历史。
8. MiniClaw 事件以受限 JSONL 写入 stdout，最终结果或稳定错误作为唯一终态行。
9. Runner 标准化事件并交给既有 Trace；Agent 退出后 Coding Mini 执行隐藏 Verifier。
10. 桥和 Runner 都在 `finally` 关闭 Provider；超时由 EvalHub 终止并回收完整进程组。

MiniClaw 继续读取自己的身份、长期记忆、Skills、工具规则和 SQLite 状态。评测会话以独立
conversation ID 写入 MiniClaw 自身数据库，这是评测完整 Agent 的预期行为；EvalHub 不复制或读取
数据库正文。

## 9. JSONL 桥协议

桥只输出以下三类对象：

```json
{"type": "event", "event": {"event_type": "tool_started", "actor": "miniclaw", "message": null, "payload": {}}}
{"type": "result", "final_message": "done", "tool_call_count": 2, "version": "0.1.0", "model": "deepseek-v4-pro"}
{"type": "error", "code": "provider_error", "message": "MiniClaw provider request failed"}
```

MiniClaw 的 `turn_started`、`tool_requested`、`tool_started`、`tool_finished`、`turn_finished`、
`turn_failed` 和 `turn_cancelled` 映射到 EvalHub 白名单事件。模型增量可以聚合为有限预览，但不能
持久化内部推理内容。工具参数、工具结果和错误消息继续使用长度限制、控制字符清理和稳定字段白名单。
stdout 出现无效 JSON、多个终态或终态缺失时，Runner 返回协议错误。

stderr 只用于截断后的安全诊断，不进入正常 Trace。API Key、`.env` 内容和完整动态异常不得进入
stdout、stderr 转存、任务 JSON或结果。

## 10. 审批、错误与评分语义

- MiniClaw 缺失、未初始化、Python 不可执行或凭据未配置：`executor_not_ready`，阻塞任务且不计
  模型或 Agent 零分。
- 子进程无法启动、桥协议损坏或进程回收失败：基础设施错误，按既有任务策略处理。
- Provider 错误、Agent 最大步数、超时、工具错误和等待人工审批：样本 Runner 错误，保留 Trace 并
  继续隐藏校验。
- EvalHub 不自动批准 MiniClaw 操作。需要审批且尚未产生正确工作区时，样本按最终工作区评分为失败。
- Runner 报错后若最终工作区已经满足公开要求，隐藏 Verifier 仍可判定通过。
- 单个样本失败不阻断后续样本；协议预检仍只提供诊断，不直接替代正式样本评分。

## 11. 结果与排行榜身份

新 Agent 结果保留现有公共字段，并把 `agent` 元数据扩展为：

```json
{
  "framework": "miniclaw",
  "name": "MiniClaw",
  "version": "0.1.0",
  "model": "deepseek-v4-pro",
  "runtime_fingerprint": "sha256:...",
  "scaffold_hash": "sha256:..."
}
```

旧结果只有 `cli_version` 时继续展示；新 Pi 结果同时提供通用 `version`。MiniClaw 的
`runtime_fingerprint` 由版本、去除凭据后的有效配置以及身份、记忆和 Skills 文件摘要组成，只输出
最终 SHA-256，不输出原始内容。

Agent 排行候选身份按以下规则生成：

- Pi：`Pi · <模型>`；
- MiniClaw：`MiniClaw`。

这样历史 Pi 模型成绩保持独立，MiniClaw 的历史趋势代表完整 Agent 随版本和配置演进的表现。现有
成绩 API 的 `models` 和 `model` 字段暂时保留以兼容前端，但 Agent 模式的界面文案统一改为 Agent、
候选 Agent 和 Agent 历史成绩。

## 12. 前端行为

Agent 表单从 `/api/agents` 渲染原生选择器和就绪状态。

- Pi：显示现有模型、Provider、Base URL 和难度控件。
- MiniClaw：隐藏所有 EvalHub 模型设置，显示 MiniClaw 版本、自管模型与诊断消息。
- 不可用 Agent 可以查看原因但不能提交。
- 流程说明使用所选 Agent 名称，不再写死 Pi。
- 结果详情显示“Agent”而不是“Agent 壳”，并展示通用版本与自管模型。
- Agent 排行不再使用“Agent 基模”文案。

页面继续复用现有轮询、任务列表、节点检查器、Trace、失败样例、难度报告和六维图，不增加新页面或
状态库。

## 13. 兼容性

- `agent_framework="pi"` 的历史请求和新请求行为不变。
- `TaskRequest` 字段和 SQLite 表结构不变，不执行迁移。
- 旧结果的 `cli_version`、旧 Agent 排行记录和旧前端可选字段继续可读。
- 模型评测 API、模型 Adapter、Benchmark Registry 和非 Agent 工作流不变。
- Coding Mini 版本、样本内容、隐藏 Verifier 和评分公式不变，确保 Pi 与 MiniClaw 使用同一口径。

## 14. 测试策略

自动化测试不访问真实网络、Ollama 或 MiniClaw Provider：

1. Registry 固定返回 Pi、MiniClaw，并用临时目录验证就绪和不可用诊断。
2. API 接受两类合法请求，拒绝未知 Agent 和交叉模型配置。
3. Worker 按 Agent ID 构造正确 Runner，不把 MiniClaw 凭据或伪模型参数传入 Benchmark。
4. MiniClaw Runner 使用 Fake Process 验证命令、stdin、JSONL 事件、结果、无效协议、非零退出、超时
   和进程回收。
5. 无头桥的纯事件转换与脱敏逻辑使用合成 MiniClaw 事件验证。
6. Coding Mini 使用 Fake Runner 验证两种 Agent 都走同一预检、工作区、Verifier 和聚合路径。
7. 排行验证历史 Pi 按模型分组、MiniClaw 按完整 Agent 分组，模型评测不受影响。
8. 前端验证 Agent 切换、条件表单、请求正文、不可用状态、结果元数据和排行榜文案。

本地集成验证可以单独执行 MiniClaw `describe` 和一个显式标记的真实 Coding Mini 样本；它不属于
默认 pytest，只有用户明确允许使用本地凭据和网络时运行。

交付前执行：

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

## 15. 验收标准

- 页面可选择 Pi 或 MiniClaw，并只展示各自需要的设置。
- MiniClaw 可在不修改其源码的情况下完成 Coding Mini 样本。
- EvalHub 不读取、返回或持久化 MiniClaw 明文凭据。
- Pi 与 MiniClaw 的最终得分都只来自相同隐藏 Verifier。
- Trace 能区分 Agent 来源并展示标准化工具与终态事件。
- MiniClaw 不可用时明确阻塞，不能产生虚假零分或虚假成功。
- 历史 Pi 任务、模型评测和现有 API 继续通过全部回归检查。
