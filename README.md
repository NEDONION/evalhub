# EvalHub

EvalHub 是一个面向企业大模型研发流程的统一评测基础设施。它的目标不是做简单 CRUD 后台，而是把 Model Registry、Dataset Registry、Benchmark Registry、Evaluator Plugin、Evaluation Engine、Result Store、Report、Leaderboard 和 Release Gate 串成一条可追踪、可复现、可扩展的评测链路。

当前仓库先落地 Python 后端核心骨架和一个 React 本地 Web 控制台，保证真实公开 Benchmark 可以下载到本地、用本地模型服务试跑，并产出样本级结果和聚合分数。后续再接入 FastAPI、PostgreSQL、Celery、RabbitMQ 和 MinIO。

## 当前能力

- 模型适配器抽象：统一不同模型、本地推理服务和 API 模型的调用入口。
- 评测器插件抽象：内置 `exact_match`，后续可扩展 Rouge、BLEU、LLM-as-a-Judge、Safety、Agent Eval。
- Benchmark 配置模型：表达 Dataset + Evaluator + Config 的组合。
- Evaluation Runner：按样本执行推理、打分、聚合报告。
- 内存 Registry：用于 MVP、单元测试和后续数据库 Repository 的接口参考。
- 真实数据集：支持下载并本地缓存 `GSM8K test` 和 `MMLU test`。
- 本地模型：支持调用 Ollama 本地模型服务，并在页面内选择、下载、观察进度或取消推荐模型。
- 本地控制台：用侧边栏区分概览、发起评测、资产管理和评测结果；一个 Python 进程同时提供构建后的前端页面和后端 API。
- 数据集资产：已缓存 Benchmark 可从页面强制更新，下载完成后反馈实际样本数。

## 30 秒理解 EvalHub

```mermaid
flowchart LR
    User[开发者 / 评测负责人]
    Entry{使用入口}
    Web[Web Console]
    CLI[CLI]
    Engine[Evaluation Engine]
    Dataset[(Dataset)]
    Benchmark[Benchmark Config]
    Model[Model Adapter]
    Evaluator[Evaluator Plugin]
    Result[(样本级结果)]
    Report[聚合报告]

    User --> Entry
    Entry --> Web
    Entry --> CLI
    Web --> Engine
    CLI --> Engine
    Dataset --> Engine
    Benchmark --> Engine
    Engine --> Model
    Model --> Engine
    Engine --> Evaluator
    Evaluator --> Result
    Result --> Report
```

### 当前本地 MVP 架构

```mermaid
flowchart TB
    subgraph Client["交互层"]
        Browser["浏览器<br/>frontend/"]
        Command["CLI<br/>src/evalhub/cli.py"]
    end

    subgraph Local["本地 EvalHub 进程"]
        Server["HTTP Server<br/>src/evalhub/server.py"]
        subgraph Orchestration["Web / CLI 编排边界"]
            Workflow["run_real_benchmark()<br/>准备、加载、创建记录与依赖"]
        end
        Loader["Dataset Loader<br/>src/evalhub/datasets/"]
        Registry["InMemory Registry<br/>src/evalhub/registry/"]
        Runner["Evaluation Runner<br/>src/evalhub/engine/"]
        Eval["Evaluator Registry<br/>src/evalhub/evaluators/"]
        Adapter["Model Adapter<br/>src/evalhub/adapters/"]
    end

    Public[("GSM8K / MMLU")]
    Cache[("data/ 本地缓存")]
    Ollama["Ollama API<br/>127.0.0.1:11434"]

    Browser -->|"HTTP / JSON"| Server
    Server --> Workflow
    Command --> Workflow
    Workflow -->|"准备并加载"| Loader
    Public -->|"首次下载"| Cache
    Cache -->|"加载样本"| Loader
    Loader -->|"返回 samples"| Workflow
    Workflow -->|"创建 Model / Dataset / Benchmark / Job"| Registry
    Workflow -->|"create evaluator"| Eval
    Eval -->|"返回 Evaluator"| Workflow
    Workflow -->|"注入 adapter、evaluator、job、benchmark、samples"| Runner
    Runner --> Adapter
    Adapter -->|"POST /api/generate"| Ollama
    Ollama -->|"模型输出"| Adapter
```

当前 MVP 是单机实现。Web 与 CLI 复用同一条 Python 评测核心链路；运行已构建页面只需要 Python，修改 React 前端时需要 Node.js 20+。

`data/` 和 Ollama 都在本机；本图只展示当前实现，不把规划中的组件混入其中。

## 快速开始

```bash
cd /Users/nedonion/PycharmProjects/evalhub
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
evalhub run-example
```

如果暂时不安装依赖，也可以直接运行核心示例：

```bash
cd /Users/nedonion/PycharmProjects/evalhub
.venv/bin/python run_evalhub.py run-example
```

或者显式指定 `src` 包路径：

```bash
PYTHONPATH=src .venv/bin/python -m evalhub.cli run-example
```

## 真实 Benchmark 试跑

当前支持两个真实主流公开数据集：

- `gsm8k`：OpenAI Grade School Math，官方 GitHub `test.jsonl`。
- `mmlu`：Hendrycks MMLU，官方 `data.tar`，默认先跑 `abstract_algebra` 科目。

```mermaid
flowchart TD
    Start([发起评测]) --> Input[选择数据集、模型与样本范围]
    Input --> Limit{样本范围}
    Limit -->|全部 all| All[limit = None]
    Limit -->|快速 quick| Quick[limit = 5]
    Limit -->|自定义 custom| Custom[limit = 用户输入]
    All --> Prepare
    Quick --> Prepare
    Custom --> Prepare
    Prepare[准备数据集] --> Prepared{数据可用?}
    Prepared -->|否| DataError["返回准备或加载错误<br/>尚未创建 Job"]
    Prepared -->|是| Load[加载并标准化 EvaluationSample]
    Load --> Loaded{样本可用?}
    Loaded -->|否| DataError
    Loaded -->|是| Records[创建 Model / Dataset / Benchmark / Job 记录]
    Records --> Running[Job 标记为 running]
    Running --> Loop[逐样本执行]
    Loop --> Infer[Model Adapter 调用 Ollama]
    Infer --> Score[Evaluator 对照 reference 打分]
    Score --> SampleResult[生成 EvaluationSampleResult]
    SampleResult --> More{还有样本?}
    More -->|是| Loop
    More -->|否| Aggregate[聚合 EvaluationReport]
    Aggregate --> Success[Job 标记为 success]
    Success --> Output[CLI JSON 或 Web 结果面板]
    Infer -->|"runner.run() 内服务或推理异常"| Failed["Job 标记为 failed"]
    Score -->|"runner.run() 内评分异常"| Failed
    Failed --> Error[返回错误信息]
```

查看支持的数据集：

```bash
cd /Users/nedonion/PycharmProjects/evalhub
.venv/bin/python run_evalhub.py list-datasets
```

下载并缓存真实数据集：

```bash
.venv/bin/python run_evalhub.py prepare-dataset gsm8k
.venv/bin/python run_evalhub.py prepare-dataset mmlu
```

使用 Ollama 本地模型跑 GSM8K：

```bash
ollama serve
ollama pull qwen2.5:0.5b

cd /Users/nedonion/PycharmProjects/evalhub
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset gsm8k \
  --adapter ollama \
  --model qwen2.5:0.5b \
  --limit 5
```

不传 `--limit` 时会默认跑完整数据集：

```bash
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset gsm8k \
  --adapter ollama \
  --model qwen2.5:0.5b
```

使用 Ollama 本地模型跑 MMLU：

```bash
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset mmlu \
  --adapter ollama \
  --model qwen2.5:0.5b \
  --subject abstract_algebra \
  --limit 5
```

`--adapter oracle` 只用于验证 EvalHub 管线是否正常，不代表真实模型评测。

完整文档导航见：[docs/README.md](docs/README.md)。
Ollama 安装和故障排查见：[docs/getting-started/20260804_Ollama本地模型安装与验证.md](docs/getting-started/20260804_Ollama本地模型安装与验证.md)。
Codex 对话后的文档沉淀流程见：[docs/development/20260804_Codex对话沉淀工作流.md](docs/development/20260804_Codex对话沉淀工作流.md)。

## 本地前后端一键启动

```bash
cd /Users/nedonion/PycharmProjects/evalhub
./scripts/start_local.sh
```

```mermaid
sequenceDiagram
    actor Dev as 开发者
    participant Script as start_local.sh
    participant API as Ollama API
    participant Process as Ollama Process
    participant Server as EvalHub Server
    participant Browser as 浏览器

    Dev->>Script: ./scripts/start_local.sh
    Script->>API: GET /api/tags
    alt Ollama 已运行
        API-->>Script: 模型列表
    else 已安装但未运行
        Script->>Process: ollama serve
        Process-->>Script: 后台进程 PID
        Script->>API: 再次检查 /api/tags
    else 未安装
        Script-->>Dev: 输出安装提示
    end
    Script->>Server: run_evalhub.py serve
    Server-->>Dev: http://127.0.0.1:8000
    Dev->>Browser: 打开本地控制台
    Browser->>Server: GET /api/health
    Browser->>Server: GET /api/datasets
    Browser->>Server: GET /api/ollama/status
    alt 未检测到 ollama 命令
        Server-->>Browser: installed=false（不调用 Ollama API）
    else 已安装 Ollama
        Server->>API: GET /api/tags
        alt API 可访问
            API-->>Server: 模型列表
            Server-->>Browser: 已就绪或模型缺失状态
        else API 不可访问
            Server-->>Browser: installed=true、running=false
        end
    end
```

打开：

```text
http://127.0.0.1:8000
```

这个命令会启动一个本地 Python 服务，同时提供：

- 前端页面：`frontend/dist/index.html`
- 后端 API：`/api/health`、`/api/datasets`、`/api/datasets/prepare`、`/api/evaluations/run`
- Ollama 状态 API：`/api/ollama/status`
- Ollama 下载 API：`/api/ollama/pulls`

如果检测到 Ollama 已安装但未运行，脚本会尝试自动启动 `ollama serve`，日志写入 `.runtime/ollama.log`。启动脚本会先构建 React 前端，因此首次使用需要在 `frontend/` 安装 npm 依赖。

控制台按任务分为四个目录：

- **概览**：服务、Ollama、数据集和最近得分的就绪状态。
- **发起评测**：Benchmark、模型适配器和样本范围配置。未下载模型不能发起 Ollama 评测。
- **资产管理**：查看模型大小和预计下载耗时，选择下载或暂不下载；下载中展示字节进度、速度、剩余时间和取消操作。这里也可以缓存或强制更新数据集。
- **评测结果**：集中查看运行状态、聚合指标和失败样本。

Ollama 未安装时 EvalHub Server 仍会启动，UI 会显示未就绪。数据集准备或加载失败时 API 返回错误，此时尚未创建 Job；进入 `runner.run()` 后的 Ollama 推理或 Evaluator 异常则会将 Job 状态更新为 `failed`。

## 目录结构

```text
evalhub/
├── docs/
│   ├── README.md
│   ├── getting-started/
│   │   ├── 20260804_本地运行指南.md
│   │   └── 20260804_Ollama本地模型安装与验证.md
│   ├── architecture/
│   │   ├── 20260804_系统架构.md
│   │   ├── 20260804_API接口草案.md
│   │   └── 20260804_数据模型.md
│   ├── product/
│   │   ├── 20260804_产品需求文档.md
│   │   └── 20260804_Agent评测路线图.md
│   ├── development/
│   │   └── 20260804_Codex对话沉淀工作流.md
│   └── superpowers/
│       ├── plans/
│       └── specs/
├── examples/
│   ├── benchmarks/
│   └── datasets/
├── frontend/
├── scripts/
├── src/evalhub/
│   ├── adapters/
│   ├── api/
│   ├── datasets/
│   ├── domain/
│   ├── engine/
│   ├── evaluators/
│   └── registry/
└── tests/
```

## Agent Benchmark 演进路线图

当前的 GSM8K、MMLU 评测本质上仍是一次 `Prompt → Answer` 的 LLM 补全。EvalHub 将借鉴 EvalScope 的 Agent 评测方式：先用通用 AgentLoop 包装普通 Benchmark，再让复杂 Benchmark 自带工具、环境和评分协议，最后评测 Codex、Claude Code 等完整 Agent。蓝色代表当前能力，绿色代表下一步，灰色虚线代表后续规划。

```mermaid
flowchart LR
    S1["① 单轮 LLM 补全（当前）<br/>Prompt → Answer<br/>Exact Match"]
    S2["② Native AgentLoop（下一步）<br/>Strategy + Tools + Max Steps<br/>统一 AgentTrace"]
    S3["③ Agent Benchmark Adapter<br/>Benchmark 自带 Loop / Sandbox / Scorer<br/>GAIA / SWE-bench / BFCL 类任务"]
    S4["④ External Agent Bridge<br/>Codex / Claude Code 等 Agent CLI<br/>协议转发 + 统一回放与对比"]

    S1 -->|"普通数据集进入多轮工具循环"| S2
    S2 -->|"Benchmark 定义专属环境与评分"| S3
    S3 -->|"同一任务评测完整 Agent"| S4

    classDef current fill:#e8f1ff,stroke:#1d6fd8,color:#111827,stroke-width:2px;
    classDef next fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef planned fill:#f8fafc,stroke:#94a3b8,color:#475569,stroke-dasharray:5 5;
    class S1 current;
    class S2 next;
    class S3,S4 planned;
```

有关 Agent Benchmark 的阶段目标与验收标准，请参阅 [docs/product/20260804_Agent评测路线图.md](docs/product/20260804_Agent评测路线图.md)；架构取舍见 [EvalScope 与 EvalHub Agent 评测设计 Diff](docs/architecture/20260804_EvalScope与EvalHub的Agent评测设计差异.md)。

## 下一步建议

1. 增加类型化 `NativeAgentConfig` 和统一 `AgentTrace`，不再把 Agent 配置塞进无约束字典。
2. 实现 `generate → parse → tool call → observation` 的 Native AgentLoop，让现有 Benchmark 可按配置切换到多轮评测。
3. 增加 Strategy、Tool、Environment 注册与样本级资源生命周期，先用 Fake Model/Tool 完成确定性测试。
4. 定义 `AgentBenchmarkAdapter`，让复杂 Benchmark 提供默认 Loop、Sandbox、Scorer 和最终制品提取。
5. Trace 回放稳定后再实现 External Agent Bridge，逐步接入 Codex、Claude Code 等 Agent CLI。
