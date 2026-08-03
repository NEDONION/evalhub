# Industry Benchmark Capability Profile Design

## Goal

为 EvalHub 增加基于公开行业 Benchmark 的可复现评测套件，把多个原始指标聚合为六维 LLM 能力画像，并在中文企业级工作台中展示六边形能力图、覆盖率和 Benchmark 明细。

## Product Principles

- 能力轴表达稳定的模型能力，不直接使用数据集名称作为雷达轴。
- 每个分数必须可追溯到数据集版本、提示模板、推理参数、评测器版本和样本级结果。
- 单项 Benchmark 默认执行完整测试集。快速试跑和自定义样本量必须由用户显式选择。
- 原始指标是事实来源；归一化能力分只用于跨 Benchmark 展示和模型对比。
- 未执行、不可用或失败的 Benchmark 标记为未评测，不按零分参与聚合。
- 第一版只采用本地可复现的客观评分，不使用 LLM-as-a-Judge。

## Scope

- 建立版本化 Benchmark Registry 和 Suite Registry。
- 支持原生 EvalHub 执行器与可选 `lm-evaluation-harness` 执行器。
- 预设单项评测、`LLM Industry Core v1` 和扩展评测套件。
- 将 Benchmark 原始结果归一化并聚合为六维能力画像。
- 保存套件级、Benchmark 级和样本级结果。
- 在现有中文工作台增加套件选择、能力图、覆盖率、明细和失败原因。

## Non-Goals

- 第一版不追求复刻任一公开排行榜的线上名次。
- 第一版不引入主观裁判模型或人工偏好评测。
- 第一版不在宿主机直接执行模型生成的代码。
- 本规格不实现定时调度；调度由独立规格定义并通过评测任务接口调用本能力。

## Architecture

EvalHub 采用混合评测架构：

```text
Benchmark / Suite Registry
  -> Dataset Manager
  -> Native Executor or lm-eval Executor
  -> Raw Benchmark Results
  -> Score Normalizer
  -> Capability Aggregator
  -> Report Store
  -> Workbench / Radar Chart
```

### Benchmark Registry

每个 `BenchmarkSpec` 至少包含：

- `id`：稳定标识，例如 `gsm8k`。
- `version`：EvalHub 内的协议版本，例如 `1.0.0`。
- `display_name`：中文展示名称。
- `capability`：六维能力之一。
- `dataset_source`、`dataset_revision`、`homepage`、`license`。
- `expected_sample_count` 和本地缓存路径。
- `executor`：`native`、`lm_eval` 或 `sandboxed_code`。
- `task_name`：外部执行器任务名。
- `metric`、`random_baseline`、`normalization` 和固定权重。
- `prompt_template_version`、`few_shot`、`generation_config`。
- `requirements`：额外依赖、授权或安全执行器要求。

Registry 是代码中的声明式配置，不允许前端自行推断评分规则。

### Dataset Manager

Dataset Manager 负责下载、缓存、完整性检查和状态查询。每次运行记录实际使用的来源、revision、文件摘要和样本数。数据不存在时评测任务进入新增的 `blocked` 状态，返回可执行的准备命令；不会回退到演示样本。

### Executors

- `NativeExecutor` 复用当前 Ollama 适配器和 EvalHub 评测器，适合生成式精确匹配和简单选择题。
- `LmEvalExecutor` 以隔离子进程调用固定版本的 `lm-evaluation-harness`，记录完整命令、任务版本和输出文件，并将结果转换为统一结构。
- `SandboxedCodeExecutor` 仅在安全容器就绪时运行 HumanEval/MBPP。没有隔离环境时返回 `EXECUTOR_NOT_READY`。

执行器必须实现同一接口：

```python
class BenchmarkExecutor(Protocol):
    def run(self, request: BenchmarkRunRequest) -> BenchmarkRunResult: ...
```

## Capability Model

固定六维能力如下：

| Capability ID | 中文名称 | Core Benchmarks |
| --- | --- | --- |
| `knowledge` | 知识 | MMLU-Pro、MMLU |
| `instruction_following` | 指令遵循 | IFEval |
| `mathematics` | 数学 | GSM8K、MATH-500 |
| `reasoning` | 综合推理 | BBH、ARC-Challenge、MuSR、HellaSwag |
| `coding` | 代码 | HumanEval、MBPP |
| `safety_trust` | 安全可信 | TruthfulQA、BBQ |

### Preset Suites

1. `single-benchmark`：用户选择一个 Benchmark，默认运行完整测试集。
2. `llm-industry-core-v1`：覆盖六维能力的固定核心组合，所有可用 Benchmark 默认全量运行。
3. `extended`：增加 GPQA、C-Eval、CMMLU 等专项任务，不改变核心六维定义。

扩展 Benchmark 可以进入 Registry，但只有在数据来源、评测协议和执行器均可用时才标记为 `ready`。

## Scoring and Aggregation

### Raw Scores

每个 Benchmark 首先保存官方或固定协议定义的原始指标，例如 Accuracy、Strict Prompt Accuracy、Exact Match、Pass@1。原始指标不因能力图需要而修改。

### Normalized Scores

统一展示范围为 `0-100`：

- 已在 `0-1` 范围且无随机基线的客观指标乘以 100。
- 多项选择指标采用随机基线校正：`max(0, (score - baseline) / (1 - baseline)) * 100`。
- 归一化方法必须由 `BenchmarkSpec.normalization` 明确声明并版本化。

### Capability Scores

单个能力分是该能力下所有成功 Benchmark 归一化分数的固定加权平均。返回结构同时包含：

- `score`：有有效结果时为 `0-100`，否则为 `null`。
- `status`：`complete`、`partial`、`unassessed`。
- `coverage`：成功权重除以配置总权重。
- `benchmark_results`：参与聚合的原始结果与归一化结果。

总览可以显示六维均值，但发布门禁必须基于具体 Benchmark 或能力阈值，不能只依赖单一总分。

## Reproducibility Contract

每次运行固定并保存：

- 模型名称、模型摘要或本地 Ollama tag。
- Benchmark 与数据集 revision。
- 提示模板、few-shot 设置和答案提取器版本。
- `temperature=0` 等推理参数、随机种子和最大输出长度。
- EvalHub 版本、执行器版本和运行环境摘要。
- 完整样本范围或用户显式选择的试跑范围。

Ollama 生成式协议与公开排行榜的 log-likelihood 协议不等价时，报告显示 `protocol_scope=evalhub_generation`，禁止宣称与外部排行榜分数直接可比。

## API and Result Model

新增或扩展以下接口：

- `GET /api/benchmarks`：Benchmark Registry、准备状态和执行器状态。
- `GET /api/suites`：套件定义、六维覆盖和预计样本数。
- `POST /api/datasets/prepare`：支持单个 Benchmark 或整个套件。
- `POST /api/evaluations/run`：接受 `benchmark_id` 或 `suite_id`，默认 `sample_mode=all`。
- `GET /api/evaluations/{job_id}`：任务、Benchmark 子任务、能力分和错误明细。
- `GET /api/evaluations/{job_id}/report`：完整 JSON 报告。

套件任务允许部分成功。单个 Benchmark 失败不丢弃其他结果，套件级任务状态明确扩展为 `pending`、`running`、`blocked`、`success`、`partial`、`failed` 和 `canceled`。

## Workbench Design

- 新建评测表单优先选择“评测套件”，并允许切换到“单项 Benchmark”。
- 套件详情显示六维覆盖、数据集缓存状态、执行器要求、总样本数和预计风险提示。
- 样本范围默认“全部样本”；快速试跑 5 条和自定义数量保持为显式选项。
- 报告首屏展示六边形能力图，轴固定为知识、指令遵循、数学、综合推理、代码、安全可信。
- 能力图旁展示覆盖率和 `complete/partial/unassessed`，避免残缺评测产生误导。
- 下方展示 Benchmark 原始分、归一化分、样本通过率、协议、耗时和失败原因。
- 图表使用本地资源实现，不依赖外部 CDN，并提供数值表格作为无障碍与审计视图。

## Error Handling

- 数据下载失败：保留已存在缓存，报告下载来源和可重试操作。
- Ollama 不可达或模型未安装：任务在推理前失败，不创建虚假分数。
- 外部执行器缺失：Benchmark 标记 `executor_not_ready`，套件可继续执行其他项。
- 协议不兼容：返回 `protocol_unsupported`，不静默切换评分方式。
- 样本执行失败：保存失败样本和异常；按 Benchmark 规则决定继续或终止。
- 代码沙箱缺失：HumanEval/MBPP 保持未评测，不允许宿主机降级执行。

## Testing

- Registry 测试覆盖 ID 唯一性、六维映射、固定权重和版本字段。
- Dataset Manager 测试使用临时目录验证缓存、摘要和损坏文件处理。
- 每种归一化规则先写边界测试，包括随机基线、满分和低于基线。
- Aggregator 测试覆盖完整、部分和未评测三种能力状态。
- API 测试覆盖默认全量、显式快速试跑、套件部分失败和报告结构。
- 执行器契约测试验证原生结果和 lm-eval 结果转换一致。
- 前端测试验证六个固定轴、缺失分不画成零分、中文文案和窄屏布局。
- 浏览器验收检查桌面和移动视口没有重叠、图表非空且数值与 API 一致。

## Rollout

1. 建立 Registry、统一结果结构、归一化和能力聚合。
2. 将现有 GSM8K/MMLU 迁移到 Registry，并补充原生客观 Benchmark。
3. 接入固定版本 lm-eval 执行器和剩余核心 Benchmark。
4. 增加安全代码执行器；就绪前代码能力保持未评测。
5. 上线能力图、报告明细和套件级数据准备流程。
