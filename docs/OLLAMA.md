# Ollama 本地模型安装与验证

EvalHub 的 `ollama` adapter 会调用本机 Ollama HTTP API：

```text
http://127.0.0.1:11434
```

如果页面或 CLI 出现：

```text
无法连接 Ollama 服务：http://127.0.0.1:11434
```

说明 Ollama 没有安装、没有启动，或模型还没有下载。

## macOS 安装

官方要求：

- macOS Sonoma 14 或更新版本。
- Apple Silicon 可以使用 CPU/GPU；Intel Mac 主要使用 CPU。

推荐安装方式：

1. 打开官方下载页：

```text
https://ollama.com/download/mac
```

2. 下载 macOS 版本。
3. 打开 `.dmg`，把 `Ollama.app` 拖到 `/Applications`。
4. 启动 Ollama。首次启动时，如果系统提示创建 `ollama` 命令行链接，允许它创建。

也可以使用官方脚本：

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

## 验证 CLI

安装完成后，打开一个新的终端：

```bash
ollama --version
```

如果提示 `command not found: ollama`，说明 CLI 没有加入 PATH。macOS 上 CLI 通常位于：

```text
/Applications/Ollama.app/Contents/Resources/ollama
```

可以手动创建软链接：

```bash
sudo ln -sf /Applications/Ollama.app/Contents/Resources/ollama /usr/local/bin/ollama
```

## 启动服务

方式一：直接打开 macOS 应用 `Ollama.app`。

方式二：命令行启动：

```bash
ollama serve
```

验证本地 API：

```bash
curl http://127.0.0.1:11434/api/tags
```

正常情况下会返回本地已下载模型列表。

## 下载模型

EvalHub 默认使用轻量模型：

```bash
ollama pull qwen2.5:0.5b
```

也可以换成更强模型，例如：

```bash
ollama pull qwen2.5:1.5b
ollama pull qwen2.5:7b
ollama pull llama3.2:3b
```

模型越大，占用磁盘、内存和推理时间越多。

## 运行一次 EvalHub 真实评测

准备 GSM8K：

```bash
cd /Users/nedonion/PycharmProjects/evalhub
.venv/bin/python run_evalhub.py prepare-dataset gsm8k
```

运行本地模型评测：

```bash
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset gsm8k \
  --adapter ollama \
  --model qwen2.5:0.5b \
  --limit 5
```

如果要通过页面运行：

```bash
./scripts/start_local.sh
```

然后打开：

```text
http://127.0.0.1:8000
```

页面中选择：

- 模型适配器：`Ollama 本地模型`
- 模型名称：`qwen2.5:0.5b`
- Ollama 地址：`http://127.0.0.1:11434`

`./scripts/start_local.sh` 会先检测 `http://127.0.0.1:11434/api/tags`。如果 Ollama 已安装但未启动，脚本会自动后台启动 `ollama serve`，日志写入：

```text
.runtime/ollama.log
```

## 常见问题

### 1. `无法连接 Ollama 服务`

检查服务是否启动：

```bash
curl http://127.0.0.1:11434/api/tags
```

如果连不上，启动 Ollama：

```bash
ollama serve
```

或打开 `/Applications/Ollama.app`。

### 2. 模型不存在

如果 Ollama 返回模型不存在，先拉取模型：

```bash
ollama pull qwen2.5:0.5b
```

### 3. 评测很慢

先把样本数调小：

```bash
--limit 3
```

再考虑换更小模型或更强机器。

### 4. 想先验证管线，不跑真实模型

使用 `oracle` adapter：

```bash
.venv/bin/python run_evalhub.py run-benchmark \
  --dataset gsm8k \
  --adapter oracle \
  --model oracle \
  --limit 3
```

`oracle` 只用于管线自检，不代表真实模型能力。
