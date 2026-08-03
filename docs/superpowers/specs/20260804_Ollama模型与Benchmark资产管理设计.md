# Ollama 模型与 Benchmark 资产管理设计

## 1. 背景

EvalHub 当前允许用户在控制台选择尚未安装的 Ollama 推荐模型，但页面只能提示执行
`ollama pull`。用户仍可直接发起评测，最终在推理阶段收到 HTTP 404。数据集区域也把
已缓存资产的按钮标为“更新”，实际后端准备流程却在发现本地缓存后直接返回，既不会
重新下载，也没有成功反馈，因此点击后看起来没有反应。

本设计把模型和 Benchmark 数据集统一视为“本地评测资产”：资产必须由用户显式决定
是否下载，下载过程必须可观察，更新失败时必须保留原有可用资产。

## 2. 目标

- 选择未安装模型时不自动产生网络请求，由用户明确选择“下载模型”或“暂不下载”。
- 下载前展示预计大小和透明的预计耗时区间。
- 下载中展示真实进度、已下载大小、实时速度和 ETA，并允许取消。
- 下载完成后自动刷新 Ollama 状态，使模型立即进入可评测状态。
- 未安装模型不能进入 Ollama 评测请求，避免可预防的 HTTP 404。
- Benchmark“更新”必须真正刷新本地缓存，并显示进行中、成功或失败状态。
- 模型或数据集更新失败时保留旧缓存，不把一次网络故障变成资产丢失。

## 3. 非目标

- 不实现通用下载中心、跨机器任务队列或持久化下载历史。
- 不自动安装 Ollama 应用或命令行工具。
- 不为任意第三方模型仓库实现大小探测；首版只维护 EvalHub 推荐模型的预估大小。
- 不支持把模型下载到远端 Ollama 服务；下载接口仅允许回环地址。
- 不引入 WebSocket、SSE、数据库或新的 Python 第三方依赖。

## 4. 方案选择

### 4.1 推荐方案：后台任务加短轮询

后端在独立线程中调用 Ollama `/api/pull` 流式接口，把进度保存到带锁的内存任务表；
前端每 500 毫秒查询一次任务状态。该方案与当前标准库 `ThreadingHTTPServer` 架构一致，
刷新页面后也能从后端恢复当前进度。

### 4.2 未采用：阻塞式 POST

实现最少，但长下载会占用浏览器请求，无法可靠展示进度或取消，代理和浏览器也更容易
在模型下载完成前超时。

### 4.3 未采用：SSE 或 WebSocket

实时性更高，但当前服务没有长连接基础设施。模型下载的进度刷新频率低，短轮询已经足够，
引入新的协议和生命周期管理不符合首版本范围。

## 5. 用户体验

### 5.1 模型选择

模型下拉项显示：

```text
Qwen2.5 1.5B · 约 986 MB · 已安装
Llama 3.2 3B · 约 2.0 GB · 未下载
```

选择未下载模型只改变当前选择，不自动开始下载。本地推理环境面板出现资产操作区：

```text
目标模型       Qwen2.5 1.5B
预计大小       约 986 MB
预计耗时       约 1–7 分钟（按 20–100 Mbps）

[下载模型]  [暂不下载]
```

- “下载模型”创建后台任务。
- “暂不下载”不发起网络请求，并优先切换回第一个已安装模型；没有已安装模型时保留选择，
  但 Ollama 评测按钮保持不可用。
- Oracle 管线自检不依赖 Ollama 模型，不受该限制。

### 5.2 下载进度

任务开始后操作区切换为：

```text
正在下载 qwen2.5:1.5b                         63%
[██████████████────────]
621 MB / 986 MB   31.4 MB/s   预计剩余 12 秒

[取消下载]
```

清单拉取或校验阶段还没有字节总量时展示 Ollama 返回的阶段文案，不伪造百分比。任务结束后：

- 成功：显示“模型下载完成”，刷新模型列表并解除评测限制。
- 失败：保留错误原因和“重试下载”，不删除 Ollama 已有的可恢复分层缓存。
- 取消：显示“下载已取消”，允许重新开始；取消是尽力而为，后端通过关闭流式响应和取消
  标记终止当前任务。

### 5.3 Benchmark 更新

- 未缓存数据集显示“缓存”。
- 已缓存数据集显示“更新”。
- 点击后立即显示“缓存中”或“更新中”，全局只允许一个数据集资产任务。
- 成功后在表格上方显示一次明确消息，例如“GSM8K 已更新，1,319 条样本”。
- 失败后显示错误并继续把原缓存标为“已缓存”。

## 6. 数据与 API 契约

### 6.1 模型状态

`GET /api/ollama/status` 保持现有字段，并扩展每个 `model_options` 项：

```json
{
  "name": "qwen2.5:1.5b",
  "label": "Qwen2.5 1.5B",
  "description": "轻量中文能力更好，适合本地评测入门。",
  "installed": false,
  "size_bytes": 986000000,
  "size_kind": "estimated"
}
```

- 已安装模型优先使用 Ollama `/api/tags` 返回的真实 `size`，`size_kind` 为 `actual`。
- 未安装推荐模型使用代码目录中的 `estimated_size_bytes`，`size_kind` 为 `estimated`。
- 未知本地模型如果 Ollama 没有返回大小，可以省略大小并在 UI 显示“大小未知”。

下载前耗时区间只用于容量规划：

```text
最快秒数 = size_bytes × 8 ÷ 100,000,000
最慢秒数 = size_bytes × 8 ÷ 20,000,000
```

UI 必须同时展示“按 20–100 Mbps”这一假设，不能把区间描述为确定完成时间。

### 6.2 模型下载任务

创建任务：

```http
POST /api/ollama/pulls
Content-Type: application/json

{"model":"llama3.2:3b","base_url":"http://127.0.0.1:11434"}
```

成功创建返回 HTTP 202；相同模型已有活动任务时返回现有任务，保持幂等：

```json
{
  "ok": true,
  "task": {
    "model": "llama3.2:3b",
    "status": "pulling",
    "message": "pulling manifest",
    "completed_bytes": 621000000,
    "total_bytes": 2000000000,
    "speed_bytes_per_second": 31400000,
    "eta_seconds": 44,
    "error": null
  }
}
```

查询任务：

```http
GET /api/ollama/pulls?model=llama3.2%3A3b
```

取消任务：

```http
DELETE /api/ollama/pulls?model=llama3.2%3A3b
```

任务状态为 `pending`、`pulling`、`verifying`、`success`、`failed` 或 `canceled`。内存任务表
由锁保护；同一模型最多一个活动任务，不同模型首版也通过全局执行锁限制为串行下载，避免
本地磁盘和带宽竞争。

### 6.3 数据集准备

现有接口扩展 `force`：

```http
POST /api/datasets/prepare
Content-Type: application/json

{"dataset":"gsm8k","force":true}
```

响应增加本次操作和样本数：

```json
{
  "ok": true,
  "dataset": "gsm8k",
  "path": "data/raw/gsm8k/test.jsonl",
  "operation": "updated",
  "sample_count": 1319
}
```

未缓存时前端发送 `force=false`，已缓存时发送 `force=true`。

## 7. 后端组件

### 7.1 `OllamaPullManager`

新模块负责：

- 校验模型名称和本地 Ollama 地址。
- 创建、查询和取消任务。
- 启动单个后台工作线程。
- 解析 `/api/pull` 的 NDJSON 流。
- 用单调时钟和相邻进度样本计算实时速度及 ETA。
- 把 Ollama 错误转换为稳定、可序列化的任务状态。

Server 只负责 HTTP 路由和状态码，不直接解析 Ollama 下载流。

### 7.2 推荐模型目录

`RECOMMENDED_OLLAMA_MODELS` 增加 `estimated_size_bytes`。这些值明确标记为预估值；一旦
模型安装完成，状态接口以 Ollama 返回的实际大小覆盖它。

### 7.3 安全数据集更新

`prepare_dataset(name, force=False)` 保持 CLI 的默认幂等行为。只有 UI 更新请求传入
`force=True`：

- GSM8K：下载到同目录临时文件，完整解析 JSONL 并确认存在有效样本后原子替换。
- MMLU：下载临时归档，执行现有路径安全检查，解压到临时目录并确认测试 CSV 存在；
  验证完成后交换 `data` 目录和归档。
- 下载、校验或交换失败时清理临时产物并恢复旧目录。

## 8. 前端状态与数据流

`useEvalHub` 增加独立状态：

- `modelPullTask`
- `modelPullError`
- `datasetNotice`
- `startModelPull(model)`
- `cancelModelPull(model)`
- `dismissModelDownload(model)`

轮询只在任务处于活动状态时运行；组件卸载或任务结束时清理定时器。下载成功后调用已有
`refresh()`，不在前端猜测安装状态。

`EvaluationForm` 在 `adapter=ollama` 且当前选项未安装时：

- 禁用“发起评测”。
- 显示“先下载模型或选择已安装模型”。
- 不调用 `/api/evaluations/run`。

`OllamaPanel` 负责模型资产详情与操作，不把下载按钮重复放进评测表单。

## 9. 错误与恢复

- Ollama 未安装或未运行：不显示可用下载按钮，保留现有安装/启动诊断。
- 模型名非法或地址不是回环地址：返回 HTTP 400。
- Ollama Pull 返回错误：任务进入 `failed`，保留原始安全错误摘要。
- 服务重启：内存任务消失；前端刷新后以 `/api/tags` 为最终事实。再次 Pull 由 Ollama
  复用已下载分层。
- 页面刷新：重新查询当前模型任务；没有任务时回到模型状态视图。
- Benchmark 更新失败：旧缓存继续可读，接口返回 500 和可诊断消息。

## 10. 测试策略

### Python

- 推荐模型预估大小与已安装模型真实大小覆盖。
- Pull NDJSON 解析、进度、速度、ETA 和状态映射。
- 重复创建同一任务的幂等性、全局串行限制和取消。
- 非回环地址、非法模型名和 Ollama 错误。
- GSM8K 与 MMLU 强制更新成功、下载失败、校验失败和旧缓存恢复。
- Server 的 POST、GET、DELETE 路由与 HTTP 状态码。

所有下载边界使用 Fake 响应或临时文件，不在测试中访问公网或真实 Ollama。

### React

- 未安装模型显示预估大小、耗时区间和两个选择。
- 点击“暂不下载”不调用下载 API。
- 点击“下载模型”创建任务并轮询进度。
- 进度条、大小、速度、ETA、取消、失败和完成状态。
- 完成后刷新状态并允许评测。
- 未安装模型阻止评测请求。
- 已缓存 Benchmark 点击“更新”发送 `force=true` 并展示成功消息。

### 完整验证

- Python Ruff 与 pytest。
- React Vitest、TypeScript typecheck 和生产构建。
- 用未安装的小模型进行一次本地下载交互验证；测试完成后不自动删除用户已有模型。

## 11. 交付边界

本次交付同时完成模型下载 UX 和 Benchmark 更新语义，保持当前单机轻量架构。后续如果需要
多用户、跨进程恢复或并行下载，再把内存任务表替换为持久化 Job Repository；本次接口和
状态模型可以继续复用。
