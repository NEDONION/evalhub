# 本地 Benchmark 评测故障排查

本文沉淀 EvalHub 接入真实公开 Benchmark、Ollama、`lm-evaluation-harness` 和 Docker 时的
排查经验。目标是快速区分模型能力问题、数据问题、协议不兼容和运行环境故障，避免把系统错误
记录成模型低分，也避免用非官方近似协议生成不可比较的分数。

## 先理解四种结果

| 状态 | 含义 | 是否可以重试 |
| --- | --- | --- |
| `success` | 节点完整执行并产生有效指标；得分为 0 也可以是成功 | 通常不需要 |
| `failed` | 已进入执行器，但请求、解析或评分发生非预期错误 | 修复根因后重试或新建任务 |
| `blocked` | 执行前已确认环境或协议能力不足 | 先满足前置条件 |
| `partial` | 套件部分节点成功，仍缺少至少一个能力维度 | 补齐阻塞节点后新建完整任务 |

模型答案错误只影响分数，不应让节点变成 `failed`。协议不兼容或 Docker 不可用应成为
`blocked`，而不是反复重试后显示大量失败。

## 当前执行兼容性

| 类型 | Benchmark | 当前本地执行方式 | 状态 |
| --- | --- | --- | --- |
| 原生生成/选择 | GSM8K、MMLU | EvalHub 原生执行器 + Ollama | 可运行 |
| Harness 生成 | MMLU-Pro、IFEval、MATH-500、BBH | `local-chat-completions` | 可运行 |
| Harness 多选似然 | ARC-Challenge、MuSR、HellaSwag、TruthfulQA、BBQ | 需要 prompt logprobs | Ollama 下阻塞 |
| Harness 代码 | HumanEval、MBPP | Docker + Ollama Chat Completions | Docker 就绪后可运行 |
| Hexagon 文本节点 | MMLU、IFEval、GSM8K、BBH、TruthfulQA、BBQ Mini | 固定真实来源 + 原生评估器 | 可运行 |
| Hexagon 代码节点 | HumanEval Mini | 固定 verifier Docker 镜像 | Docker 就绪后可运行 |

Ollama 的 OpenAI 兼容 `/v1/completions` 当前只接受字符串 `prompt`。同时，本机版本不会返回
官方多选似然任务所需的 prompt logprobs。接口范围以
[Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) 为准。

## 已验证的典型根因

### `/v1/completions` 返回 HTTP 400

**症状**

```text
400 Client Error: Bad Request for url: http://127.0.0.1:11434/v1/completions
```

Ollama 日志中的实际原因是 `prompt` 收到了数组，而服务端只接受字符串。

**根因**

`lm-eval` 的批量 Completions 适配器会发送 prompt 数组。MMLU-Pro、IFEval、MATH-500 和 BBH
是生成型任务，不需要 prompt logprobs，没有必要继续使用该不兼容入口。

**处理**

生成型任务统一使用 `local-chat-completions` 和 `/v1/chat/completions`。不要只捕获 400 后重试，
因为相同请求重试不会改变结果。

### 多选 Benchmark 无法得到官方分数

**症状**

ARC、MuSR、HellaSwag、TruthfulQA 或 BBQ 在推理前显示协议不兼容。

**根因**

这些官方任务通过候选续写的 log-likelihood 评分，需要 prompt logprobs。本机 Ollama 即使接受
`logprobs` 参数，也没有返回可供 Harness 评分的 prompt token logprobs。

**处理**

当前明确标记为 `blocked`。不要改成生成一个选项字母后冒充官方分数；两种协议不能直接与论文或
排行榜比较。未来接入 vLLM、Transformers 或其他后端时，应先做能力探测，再开放这些任务。

### 页面显示已缓存，运行时才发现没有数据

**症状**

资产页显示“已缓存”，但首次执行仍下载数据或直接失败。

**根因**

旧流程只执行任务名校验并写入标记文件。任务存在不代表 Hugging Face 数据已经真实加载；仅判断
标记文件存在也会把旧标记误认为有效缓存。

**处理**

- 使用 `TaskManager.load([task])` 真实构造任务和加载数据。
- 准备与执行统一使用项目内 `.runtime/huggingface` 作为 `HF_HOME`。
- 只有成功标记包含 `"preparation": "task_data_loaded"` 时，页面才显示已准备。
- 损坏、旧版或不完整标记必须重新准备，不能静默复用。

### IFEval 缺少运行依赖

**症状**

IFEval 在加载任务时报告缺少 `langdetect`，或 NLTK 报告从当前工作目录导入包。

**根因**

IFEval 有独立可选依赖。项目虚拟环境位于仓库 `.venv` 时，已观察到 NLTK 3.10.x 的工作目录
保护逻辑把 `.venv/site-packages/regex` 误判为源码目录导入。

**处理**

项目安装 `lm_eval[ifeval]`，并将 NLTK 限定在已验证的 `>=3.9.1,<3.10` 范围。依赖变化后应强制
重新准备 IFEval，再做一条样本的真实试跑。

### Hexagon 多选节点报告未知评估器

**症状**

```text
unknown evaluator type: acc
```

**根因**

标准化来源行使用 `choice_letter`，但目录曾把 MMLU、TruthfulQA 和 BBQ 声明为未注册的 `acc`。
这是 Registry、数据标准化和 Evaluator Registry 三者的合同漂移。

**处理**

三处必须使用同一个已注册标识 `choice_letter`。新增 Benchmark 时应增加目录合同测试，确认数据行
声明的评估器可以从 Registry 创建。

### HumanEval 或 MBPP 被阻塞

**症状**

代码节点显示 Docker daemon 或评测镜像未就绪。

**根因**

安装 Docker Desktop 不等于 daemon 已运行。EvalHub 还需要固定的 Harness 镜像和 Hexagon
HumanEval verifier 镜像，才能隔离执行模型生成的代码。

**处理**

```bash
docker version
docker image inspect evalhub-lm-eval:0.4.12
docker image inspect evalhub-humaneval:1.0.0
./scripts/build_humaneval_image.sh
```

`./scripts/start_local.sh` 在交互式终端会尝试打开 Docker Desktop、等待 daemon，并构建缺失镜像。
如果等待后 `docker version` 仍无法连接，先手工打开 Docker Desktop，确认 Engine Ready 后再启动。

## 为什么旧任务不能直接代表修复结果

任务节点会冻结 Benchmark、评估器、数据身份和执行参数，用于审计与断点恢复。修复协议路由或目录
映射后，旧失败节点仍保留当时的输入和错误，这是正确的审计行为。

- 单纯的临时网络错误或进程中断，可以重试旧节点。
- 评估器类型、任务协议、数据版本或提示模板已经改变时，应新建任务。
- 不要删除旧任务来隐藏故障；旧记录用于解释修复前后的差异。

## 推荐排查顺序

### 1. 检查 EvalHub 和 Ollama

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:11434/api/tags
ollama list
```

EvalHub 不可达时先检查启动日志；Ollama 不可达时执行 `ollama serve` 或打开 Ollama.app。模型不在
`ollama list` 中时先下载，不能把“模型不存在”归类为 Benchmark 失败。

### 2. 检查数据资产状态

```bash
curl http://127.0.0.1:8000/api/datasets
```

重点查看：

- `locally_runnable`：当前执行器和协议是否兼容。
- `readiness_reason`：不兼容或缺少环境时的真实原因。
- `prepared`：数据是否已经完成真实加载。
- `sample_count`：原生数据缓存能否被正常读取。

### 3. 检查 Docker 代码执行边界

```bash
docker version
docker image inspect evalhub-lm-eval:0.4.12
docker image inspect evalhub-humaneval:1.0.0
```

CLI 存在但 daemon 不可达时，镜像检查仍会失败。不要只用 `which docker` 判断就绪。

### 4. 查看节点审计，而不是只看任务总状态

在“评测结果”中展开工作流节点，依次查看：

- `node_started`、`node_failed` 或 `node_blocked` 事件。
- 节点尝试次数和耗时。
- 执行器错误原文。
- 已完成样本检查点和失败样本。

第一个失败节点通常比最终汇总状态更接近根因。后续节点的“待执行”经常只是依赖尚未满足。

### 5. 先跑一条样本，再跑全量

默认产品行为仍是完整数据集。诊断环境时先选择“快速试跑”或 CLI 的 `--limit 1`，确认数据加载、
模型调用、输出解析和评分均可完成；通过后再新建全量任务。快速试跑的分数不能替代正式全量结果。

## 修复后的验证基线

本轮修复使用以下证据验收：

- MATH-500 使用真实 Ollama 模型完成一条样本；答案错误时节点仍为 `success`，得分为 0。
- IFEval 完成真实数据准备，并使用真实 Ollama 模型完成一条样本。
- Hexagon MMLU 使用真实模型完成一条样本。
- Hexagon MMLU、TruthfulQA、BBQ 使用 Oracle 各完成一条评估器合同自检。
- `/api/datasets` 返回 20 个资产，并正确区分已准备、可运行和阻塞状态。
- Python 全量回归为 `321 passed, 9 skipped`，Ruff 和 React 生产构建通过。

测试数量会随项目演进变化；后续验收以命令退出码和零失败为准，不应把上述固定数字写进自动化判断。

## 后续接入新后端的准入条件

只有新模型后端同时满足以下条件，才能解除多选似然任务的阻塞：

1. 支持批量或逐条 prompt 的 token 级 log-likelihood。
2. 返回 prompt token logprobs，而不只是生成 token logprobs。
3. 与固定版本 `lm-eval` 的模型适配器完成真实样本验证。
4. 结果记录后端、模型版本、任务版本和协议类型。
5. 与 Ollama 生成式结果分开比较，禁止跨协议排行榜混排。

这比在现有 Ollama 路径上增加兼容分支更简单，也能保证评测结论真实、可复现、可审计。
