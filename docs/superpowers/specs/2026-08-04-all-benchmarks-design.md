# EvalHub 全量 13 Benchmark 接入设计

## 目标

让 `llm-industry-core-v1` 中的 13 个 Benchmark 都能从 React 控制台查看、缓存和提交本地评测。默认执行全部样本，并继续使用现有 SQLite 工作流记录节点状态、耗时、错误、重试和能力画像。

## 执行边界

- `GSM8K`、`MMLU`：保留现有 EvalHub 原生加载器和逐样本检查点。
- `MMLU-Pro`、`IFEval`、`MATH-500`、`BBH`、`ARC-Challenge`、`MuSR`、`HellaSwag`、`TruthfulQA`、`BBQ`：使用固定版本 `lm-eval 0.4.12` 的官方任务定义，通过 Ollama 的 OpenAI 兼容 `/v1/completions` 端点运行。
- `HumanEval`、`MBPP`：使用同版本 `lm-eval`，但整个评测进程在受限 Docker 容器中运行，生成代码不在宿主 Python 进程执行。

注册表是唯一 Benchmark 清单。MATH-500 使用当前 `lm-eval` 的 `math500` 任务名；启动和测试阶段用 `lm-eval validate` 校验全部外部任务名，避免静默使用错误协议。

## 模型与依赖

新增 `benchmarks` 可选依赖组，固定 `lm_eval[api,math,sentencepiece]==0.4.12`。一键启动脚本在缺失时安装该依赖，并在首次运行代码 Benchmark 前构建固定的本地 Docker 镜像。

Ollama 模型继续由现有下拉框选择。Registry 为推荐模型保存对应 Hugging Face tokenizer：Qwen2.5、Llama 3.2、DeepSeek-R1 Distill Qwen 和 Phi-3。未映射的自定义模型仍可用于原生生成式评测，但外部多选 Benchmark 会返回明确的 `tokenizer_not_configured` 阻塞状态。

## 资产与运行时

`prepare_assets` 按执行器分派：

1. 原生任务继续下载到 `data/raw` 并计算内容 SHA-256。
2. `lm-eval` 任务加载官方任务定义和 Hugging Face 数据集，缓存到 `.runtime/huggingface`，成功后写入轻量资产标记。
3. 代码任务额外检查 Docker CLI/Daemon 和本地评测镜像。

外部任务完成后，把 `lm-eval` 的 `results` 与 `samples` 转换成现有 Benchmark 输出和 SQLite 样本记录。外部 Harness 只在一次 Benchmark 调用成功返回后批量持久化样本；节点失败时重试整个 Benchmark，成功节点和其他 Benchmark 不会重跑。原生 GSM8K/MMLU 保持当前逐样本断点能力。

## Docker 约束

HumanEval/MBPP 容器使用只读根文件系统、临时 `/tmp`、CPU/内存/PID 限制、移除 Linux capabilities 和 `no-new-privileges`。容器只挂载 Hugging Face 缓存与本次输出目录。Ollama 地址从本机回环地址转换为 `host.docker.internal`。

## API 与 UI

`GET /api/datasets` 改为从 Benchmark Registry 返回全部 13 项，并附带执行器、能力维度、缓存状态、样本数和准备提示。`POST /api/datasets/prepare` 支持全部 13 项。

`GET /api/benchmarks` 和 `GET /api/suites` 根据依赖、tokenizer、Docker 状态计算真实可运行数量。前端资产表显示全部 13 项，单项下拉不再禁用已接通的外部任务，Suite 提示显示 `13 / 13`；准备、失败和阻塞状态使用中文文案。

## 验证

- 单元测试覆盖 13 项资产 API、执行器分派、Harness 结果转换、Docker 命令安全参数和 UI 展示。
- `lm-eval validate` 验证 11 个外部任务定义可加载。
- 运行 Ruff、完整 pytest、前端测试、类型检查和生产构建。
- 本地启动后调用 API，并用浏览器确认资产页展示 13 项、表单可选全部 Benchmark。
