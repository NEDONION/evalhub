# Local Run Guide

## 目标

本地真实试跑 EvalHub，需要三件事：

1. 下载真实公开 Benchmark 数据集到本地。
2. 启动一个本地模型服务。
3. 通过 CLI 或 Web 控制台发起评测。

当前实现不依赖 `datasets`、`pandas`、`pyarrow`、`FastAPI` 或前端构建工具，核心试跑只需要 Python 标准库。

## 支持的数据集

| 名称 | 来源 | 本地缓存 | 指标 |
| --- | --- | --- | --- |
| `gsm8k` | OpenAI grade-school-math official GitHub | `data/raw/gsm8k/test.jsonl` | `numeric_exact_match` |
| `mmlu` | Hendrycks MMLU official data archive | `data/raw/mmlu/data/test` | `choice_letter` |

## 准备数据集

```bash
cd /Users/nedonion/PycharmProjects/evalhub
.venv/bin/python run_evalhub.py prepare-dataset gsm8k
.venv/bin/python run_evalhub.py prepare-dataset mmlu
```

第一次运行会从公网下载数据；后续会复用 `data/raw` 下的本地缓存。

## 准备本地模型

推荐先用 Ollama，因为它提供本地 HTTP API，EvalHub 可以直接调用：

```bash
ollama serve
ollama pull qwen2.5:0.5b
```

如果你已经有更大的本地模型，可以把 `--model` 换成对应模型名。

完整安装、PATH 配置、API 验证和故障排查见：

```text
docs/OLLAMA.md
```

## CLI 试跑

GSM8K：

```bash
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset gsm8k \
  --adapter ollama \
  --model qwen2.5:0.5b \
  --limit 5
```

MMLU：

```bash
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset mmlu \
  --adapter ollama \
  --model qwen2.5:0.5b \
  --subject abstract_algebra \
  --limit 5
```

输出字段：

- `status`：任务状态。
- `metric`：使用的 evaluator。
- `total_samples`：本次样本数。
- `passed_samples`：通过样本数。
- `average_score`：平均分。
- `failed_examples`：最多 5 条失败样例，便于 bad case 分析。

## Web 控制台

```bash
cd /Users/nedonion/PycharmProjects/evalhub
./scripts/start_local.sh
```

浏览器打开：

```text
http://127.0.0.1:8000
```

页面支持：

- 查看数据集是否已准备。
- 点击下载真实数据集。
- 选择数据集、模型、样本数。
- 发起本地评测。
- 查看 JSON 结果和失败样例。

## 注意

`oracle` adapter 是管线自检模式，会直接返回参考答案，只能证明 EvalHub 数据加载、Runner 和 Evaluator 正常，不能作为模型能力评测结果。
