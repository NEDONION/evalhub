# EvalScope 与 EvalHub Agent 评测设计 Diff

## 1. 对比范围

本文聚焦 Agent 评测，不比较 EvalScope 的 VLM、AIGC、压测、Arena 等其他能力。

- EvalScope 源码：本地仓库 `/Users/nedonion/PycharmProjects/evalscope`
- EvalScope 基线：`45b52392`
- EvalHub 基线：当前 `main` 上的同步单轮评测实现
- 目标：借鉴 EvalScope 的设计边界和扩展方式，不直接复制实现代码

EvalScope 使用 Apache-2.0 许可证。本文只总结架构思想；后续如果直接移植源码，需要保留许可证与版权声明。

## 2. 核心结论

EvalHub 当前是一个以 `Model / Dataset / Benchmark / Job / Result` 为中心的评测平台领域骨架；EvalScope 则是一个以 `TaskConfig / DataAdapter / Model / Evaluator` 为中心、依赖注册机制扩展的评测执行框架。

两者并不冲突。EvalHub 应保留自己的 Job、Repository 和报告模型，同时借鉴 EvalScope 的 Agent 执行内核：

1. 用一个类型化 `agent_config` 在单轮、Native AgentLoop、External Agent 之间切换。
2. 让普通 Benchmark 可以直接套用通用 AgentLoop。
3. 让复杂 Agent Benchmark 自己提供策略、工具、环境和最终结果提取方式。
4. Native 与 External 两条执行路径都产出同一种 `AgentTrace`。
5. Agent 最终产物继续进入原有 Evaluator 和 Report 链路，不另造一套评分系统。

## 3. 总体设计思路差异

| 维度 | EvalScope | EvalHub 当前 | 对 EvalHub 的启示 |
| --- | --- | --- | --- |
| 产品定位 | 可直接运行大量 Benchmark 的评测框架 | 面向评测平台的领域与服务骨架 | 保留平台模型，补齐可执行的 Agent 内核 |
| 核心入口 | `TaskConfig + run_task()` | CLI/Web 装配 `EvaluationRunner` | 给 Job 增加类型化 Agent 运行配置 |
| 扩展中心 | Benchmark、Model、Metric、Strategy、Tool、Environment、Runner 注册表 | Model Adapter、Evaluator Registry、Repository | 不需要一次复制全部 Registry，先补 Agent 所需的四类注册表 |
| Benchmark 责任 | DataAdapter 负责样本转换、推理钩子、答案提取和评分衔接 | `BenchmarkRecord` 主要是 Dataset + Evaluator + Config 数据 | 增加可执行的 `BenchmarkAdapter`，不能只保存配置 |
| 单轮推理 | `Model.generate()` 返回结构化 `ModelOutput` | `ModelAdapter.generate(prompt) -> str` | Agent 支持的前置条件是结构化消息、工具调用和用量 |
| Agent 推理 | 通用 AgentLoop、Benchmark 自定义 Loop、外部 Agent Bridge | 不存在；每条样本只生成一次 | 先实现通用 Native AgentLoop，再扩展另外两种路径 |
| 样本模型 | 输入可为字符串或消息，附带 Tools、Files、Setup、Sandbox、Metadata | 只有 `input: str`、`reference: str` 和 Metadata | 保持字符串兼容，同时增加 Agent 场景所需结构 |
| 运行环境 | 每样本创建并关闭 `AgentEnvironment`，支持本地和沙箱 | 没有环境抽象 | 环境必须是样本级资源，并保证异常时释放 |
| 轨迹 | `AgentTrace` 是缓存、Review 和 Web 回放的一等数据 | 只保存输入、预测、参考答案和分数 | Trace 应进入领域结果，而不是只写日志 |
| 失败处理 | 解析、工具、超时、步数上限等进入 Trace；样本可继续完成评分 | 任意样本异常会让整个 Job 失败 | 区分样本失败、Agent 终止和 Job 基础设施失败 |
| 测试方式 | 接口契约、Loop 单测、Bridge Walking Skeleton、Evaluator 端到端 | 以同步 Runner 和边界适配器单测为主 | 按层测试，外部边界全部用 Fake，不依赖网络和真实模型 |

## 4. 执行链路 Diff

### 4.1 EvalHub 当前链路

```text
EvaluationSample
  -> EvaluationRunner
  -> ModelAdapter.generate(prompt) -> str
  -> Evaluator.evaluate(prediction, reference)
  -> EvaluationSampleResult
  -> EvaluationReport
```

这条链路适合 GSM8K、MMLU 等单次补全任务，但表达不了消息历史、工具定义、工具返回值、环境状态、步数预算和终止原因。

### 4.2 EvalScope Native AgentLoop 链路

```text
TaskConfig.agent_config(mode=native)
  -> DefaultDataAdapter / AgentLoopAdapter
  -> Strategy + Tool Registry + Environment
  -> AgentLoop
       generate -> parse -> tool call -> observation -> generate
  -> InferenceResult(output + messages + AgentTrace)
  -> 原 Benchmark 的答案提取与评分
  -> Review / Report / Trace Replay
```

关键点是：AgentLoop 只负责编排循环，Strategy 决定提示词、输出解析和结束条件，ToolExecutor 只负责执行工具，Environment 只提供隔离的执行表面。

### 4.3 EvalScope External Agent Bridge 链路

```text
TaskConfig.agent_config(mode=external)
  -> AgentRunner 启动 Codex / Claude Code 等 CLI
  -> 本地协议 Bridge 截获 CLI 的模型请求
  -> EvalScope Model
  -> Bridge 记录统一 AgentTrace
  -> AgentRunner 返回最终输出或沙箱制品
  -> 原 Benchmark 评分链路
```

External 模式的价值是评测完整 Agent 产品，而不只是评测底层模型是否会调用工具。它需要协议代理、CLI 生命周期、鉴权、流式事件和沙箱网络，因此不应成为 EvalHub 的第一阶段。

## 5. 核心契约 Diff

| EvalScope 契约 | EvalScope 的责任 | EvalHub 当前对应物 | 差距 |
| --- | --- | --- | --- |
| `TaskConfig.agent_config` | 用 `native/external` 判别联合类型选择执行模式 | `EvaluationJob.runtime_config: dict` | 缺少类型、校验和明确分派 |
| `Sample` | 字符串/消息输入、工具、文件、沙箱和 Metadata | `EvaluationSample` | 样本只能表示问答补全 |
| `ModelOutput` | 消息、Tool Calls、Stop Reason、Token Usage、Metadata | `str` | AgentLoop 无法解析动作或统计开销 |
| `InferenceResult` | 统一携带最终输出、完整消息和 Trace | 无 | Agent 信息无法回到评分与持久化层 |
| `AgentStrategy` | 构造 Prompt、暴露工具、解析动作、判断结束 | 无 | 交互协议会被迫写死在 Runner 中 |
| `ToolExecutor` | 按名称分派工具并把错误转为 Observation | 无 | 工具异常只能抛出，Agent 无法自我恢复 |
| `AgentEnvironment` | 样本级 `exec/close` 与资源隔离 | 无 | 无法安全运行 Bash、Python 或代码任务 |
| `AgentTrace` | 记录生成、工具、环境、错误、提交和用量事件 | `EvaluationSampleResult` | 只有最终字符串，没有过程证据 |
| `AgentLoopAdapter` | Benchmark 提供默认策略、工具、环境和答案提取 | `BenchmarkRecord` | Benchmark 是数据记录，不是可执行插件 |
| `AgentRunner` | 注册并启动第三方 Agent CLI | 无 | 无法评测 Codex、Claude Code 等完整 Agent |

## 6. EvalScope 最值得借鉴的设计

### 6.1 普通 Benchmark 可通过配置升级为 Agent 评测

EvalScope 没有要求为 GSM8K 重新写一套 Agent 版本。只要设置 Native Agent 配置，原 DataAdapter 的单轮调用就切换为 AgentLoop，最终答案仍交给 GSM8K 原有评分器。

EvalHub 应保持同样性质：`agent_config=None` 继续走现有同步补全；设置 Native 配置后，仅替换推理执行方式，不改变 Dataset Loader、Evaluator 和 Report。

### 6.2 Benchmark 默认值优先，用户配置只做显式覆盖

SWE-bench 等复杂 Benchmark 必须拥有自己的镜像、工具和提交协议。EvalScope 允许 Benchmark 提供默认值，同时让用户显式覆盖 Strategy、Max Steps 或额外工具，并确保 Benchmark 自带的关键 Tool Handler 不会被同名全局工具替换。

EvalHub 后续的 Agent Benchmark Adapter 也应遵守：

- Benchmark 拥有完成任务所必需的环境和验证器。
- Job 配置只覆盖公开允许调整的参数。
- 关键安全与判分语义不能被运行时配置静默替换。

### 6.3 Native 与 External 共用 Trace 模型

EvalScope 的两条 Agent 路径最终都写入 `AgentTrace`，并用 `framework` 区分来源。UI、缓存和报告不需要理解具体 Agent 的内部实现。

EvalHub 应先稳定 Trace 契约，再增加更多 Agent 类型。建议的首批事件：

- `model_generate`
- `tool_call`
- `tool_result`
- `environment_exec`
- `error`
- `submit`
- `run_start`
- `run_end`

### 6.4 Agent 终止是显式结果，不用异常控制正常流程

EvalScope 使用 `final_answer` 作为统一终止信号；达到最大步数、上下文溢出和工具错误会写入 Trace。环境释放放在 `finally` 或异步上下文中。

EvalHub 应区分：

- 正常提交：样本完成并进入评分。
- 预算耗尽：样本产生失败结果和 Trace，但不一定让整个 Job 崩溃。
- 工具错误：转换为 Observation，允许 Agent 重试。
- 环境或平台错误：标记基础设施失败，按 Job 策略决定是否中止。

### 6.5 外层同步、内层异步

EvalScope 让 AgentLoop 保持异步，再通过统一桥接器接入同步 Evaluator。这样工具、MCP 和流式模型可以自然扩展，同时不会立即重写整个评测主流程。

EvalHub 可以沿用当前同步 `EvaluationRunner` 外壳，第一阶段只在单样本 Agent 执行器内部使用异步；等需要样本并发和分布式 Worker 时再提升调度层。

## 7. 不应现在照搬的能力

| 暂缓项 | 原因 |
| --- | --- |
| 完整 External Agent Bridge | 涉及 Anthropic、OpenAI Responses、Gemini 等协议和流式转换，远超第一个可用 Agent Benchmark 的需求 |
| 自动安装多种 Agent CLI | 冷启动、供应链、版本兼容和权限风险高 |
| 完整 Docker/远端沙箱管理器 | 先用 Fake 和受限本地环境验证契约，再引入容器依赖 |
| 全量 Benchmark Registry | EvalHub 当前规模小，先实现 `AgentBenchmarkAdapter` 接口和少量显式注册即可 |
| 全量 MCP 传输协议 | 第一阶段先验证内置 Python 工具；MCP 属于工具来源扩展，不是 Loop 正确性的前提 |
| 直接复制 EvalScope Pydantic 模型 | EvalHub 领域层当前使用 dataclass；应借鉴字段与边界，保持自身依赖和分层规则 |

## 8. 建议的 EvalHub 目标结构

```text
src/evalhub/
├── agent/
│   ├── types.py          # AgentConfig、Message、Action、Trace、RunResult
│   ├── loop.py           # generate -> parse -> act -> observe 循环
│   ├── strategy.py       # AgentStrategy 协议与 Function Calling 实现
│   ├── tools.py          # Tool 协议、ToolExecutor 与注册表
│   ├── environment.py    # AgentEnvironment 协议与生命周期
│   └── external/         # 后续 External Agent Runner 与 Bridge
├── benchmarks/
│   ├── base.py           # BenchmarkAdapter
│   └── agent.py          # AgentBenchmarkAdapter
├── adapters/             # 结构化 ModelOutput 的模型边界
├── engine/               # Job 编排与单轮/Agent 分派
└── domain/               # Job、Result、Report 与 Trace 持久化实体
```

保持兼容的执行分派：

```text
agent_config is None
  -> 现有 ModelAdapter.generate(prompt) 单轮链路

agent_config.mode == native
  -> NativeAgentRunner -> AgentLoop -> AgentRunResult

agent_config.mode == external
  -> ExternalAgentRunner -> Bridge -> AgentRunResult（后续阶段）

所有路径
  -> Evaluator -> EvaluationSampleResult -> EvaluationReport
```

## 9. 分阶段落地顺序

### Phase 0：单轮基线（当前）

- GSM8K、MMLU
- `ModelAdapter.generate(prompt) -> str`
- Exact Match、Numeric Match、Choice Evaluator
- 样本结果与 JSON Report

### Phase 1：Native AgentLoop 最小闭环

- 增加类型化 `NativeAgentConfig`
- 引入结构化 Message、Tool Call、Model Output
- 实现 `AgentStrategy`、`ToolExecutor`、`AgentLoop`
- 支持 Max Steps、Submit、Tool Error Observation
- 产生并持久化 `AgentTrace`
- 用 Fake Model + Fake Tool 跑通单样本测试

验收：同一个基础 Benchmark 在不改 Evaluator 的情况下，可由配置切换为多轮工具评测。

### Phase 2：Benchmark 自带工具与沙箱

- 增加 `AgentBenchmarkAdapter`
- Benchmark 可以提供默认 Strategy、Tools、Environment 和最终制品提取
- 实现样本级环境创建、重置和关闭
- 先接一个轻量代码或终端任务，使用受控测试环境

验收：Benchmark 可以验证环境最终状态，而不只比较 Agent 的最终文本。

### Phase 3：真实 Agent Benchmark 与 Trace 回放

- 接入 Function Calling 类任务
- 接入 GAIA 类通用工具任务
- 接入 SWE-bench Mini 类代码修复任务
- Web 控制台按 Step 展示模型生成、工具调用、观察、错误和提交

验收：失败样例可以从报告进入完整轨迹，定位模型、工具或环境中的失败步骤。

### Phase 4：External Agent Bridge

- 定义 `AgentRunner` 注册协议
- 先支持一个 External Runner，再逐步扩展 Codex、Claude Code 等 CLI
- Bridge 负责协议适配、Trial 鉴权和统一 Trace
- 同一 Benchmark 可比较不同 Agent，或比较同一 Agent 的不同底层模型

验收：不修改外部 Agent 源码即可完成样本执行、评分与轨迹回放。

## 10. 测试策略 Diff

EvalHub 应按 EvalScope 的分层测试思想补齐以下测试，而不是依赖真实 Ollama、Docker 或网络：

1. **接口契约测试**：配置校验、注册表重复项、Trace JSON 往返、环境抽象。
2. **Loop 单元测试**：直接提交、工具后提交、未知工具、超时、步数耗尽、系统提示只注入一次。
3. **资源生命周期测试**：成功、工具异常、模型异常时环境都关闭；调用方拥有的环境不被误关。
4. **Runner 管线测试**：Fake Model + Fake Tool + 现有 Evaluator，验证 Trace 和 Score 同时写入结果。
5. **Walking Skeleton**：External 阶段用 Mock Runner 和随机本地端口走完整 Bridge，不连接真实模型或付费 API。
6. **Benchmark 契约测试**：Benchmark 默认工具优先、显式配置覆盖、最终制品提取和官方评分器衔接。

## 11. 主要风险

- **ModelAdapter 兼容性**：从字符串返回值升级到结构化输出会影响现有 Ollama 和 Static Adapter，必须提供兼容层。
- **Trace 体积**：工具输出、命令日志和消息历史可能很大，需要预览截断与 Artifact 外置策略。
- **敏感数据**：Trace 可能包含环境变量、文件内容和工具参数，持久化前必须脱敏。
- **沙箱安全**：本地环境不能作为生产默认值；Shell、网络和文件挂载必须显式授权。
- **可复现性**：Agent 具有随机性，正式比较需要记录模型参数、Tool/Environment 版本、Seed 和重复运行次数。
- **评分语义**：Agent Benchmark 应优先验证最终环境状态，轨迹质量分只能作为辅助指标，不能替代任务成功判定。

## 12. 源码证据索引

以下文件来自本地 EvalScope 基线 `45b52392`：

| 主题 | EvalScope 文件 |
| --- | --- |
| Agent 配置 | `evalscope/config.py`、`evalscope/api/agent/types.py`、`evalscope/agent/external/config.py` |
| AgentLoop | `evalscope/api/agent/loop.py`、`evalscope/api/agent/runner.py` |
| Strategy / Tool / Environment | `evalscope/api/agent/strategy.py`、`tool_executor.py`、`environment.py` |
| Native 装配 | `evalscope/agent/runner.py` |
| Agent Benchmark Adapter | `evalscope/api/benchmark/adapters/agent_adapter.py` |
| External Bridge | `evalscope/agent/external/adapter.py`、`bridge/`、`runners/` |
| Trace 持久化 | `evalscope/api/agent/trace.py`、`api/evaluator/state.py`、`api/evaluator/cache.py` |
| 测试 | `tests/agent/`、`tests/benchmark/test_agent_loop.py` |

EvalHub 当前对照文件：

- `src/evalhub/domain/entities.py`
- `src/evalhub/adapters/base.py`
- `src/evalhub/engine/runner.py`
- `src/evalhub/evaluators/base.py`
- `src/evalhub/evaluators/registry.py`
- `tests/test_runner.py`
