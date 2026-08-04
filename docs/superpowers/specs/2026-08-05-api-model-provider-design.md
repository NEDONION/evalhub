# API 模型服务商接入设计

## 目标

让用户在 EvalHub Web 控制台中配置并持久复用远程模型服务商，通过统一的
OpenAI-compatible 协议运行模型评测。首批内置 DeepSeek、硅基流动和 Kimi，同时
允许添加自定义 OpenAI-compatible 服务商。

API Key 必须经过认证加密后落盘。浏览器、任务记录、日志、错误消息和 API 响应均不得
获得已保存的完整密钥。

## 非目标

- 首期不让远程 API 模型参与 Pi Agent 评测，Agent 继续固定使用本地 Ollama。
- 首期不宣称所有 `lm-eval` 节点支持远程 API；依赖特殊 prompt logprobs 或现有
  Ollama/Docker 协议的节点保持明确阻塞。
- 不实现厂商专属 SDK、流式输出、图像输入、工具调用或厂商计费面板。
- 不把 API Key 写入评测请求、SQLite 任务 JSON、子进程事件、报告或可复现元数据。

## 方案选择

采用“服务商配置仓储 + 单一 OpenAI-compatible Adapter”。内置服务商只是带稳定 ID 和
默认地址的预设，自定义服务商复用完全相同的数据结构和调用链。

不为每家厂商创建独立 Adapter，避免重复认证、请求、重试和错误解析逻辑。也不要求用户
每次创建评测时重新填写连接信息，避免密钥进入任务历史。

## 内置服务商

| 稳定 ID | 展示名称 | 默认 Base URL |
| --- | --- | --- |
| `deepseek` | DeepSeek | `https://api.deepseek.com` |
| `siliconflow` | 硅基流动 | `https://api.siliconflow.cn/v1` |
| `kimi` | Kimi | `https://api.moonshot.ai/v1` |

这些地址分别遵循厂商当前官方 OpenAI-compatible 文档。内置配置可以更新 Base URL 和
API Key，也可以重置为默认地址；稳定 ID 不可删除。自定义配置使用项目现有 ID 生成模式，
可以创建和删除。

## 数据模型与持久化

新增独立的模型服务商仓储，不把凭据职责塞入已经较大的任务仓储。默认数据库位于
`.runtime/model_providers.sqlite3`，并继续受仓库 `.gitignore` 保护。

服务商记录包含：

- `id`：内置稳定 ID 或自定义生成 ID。
- `name`：Web 展示名称。
- `kind`：`builtin` 或 `custom`。
- `base_url`：已经规范化的 API 根地址。
- `encrypted_api_key`：认证加密后的密文，只在仓储内部出现。
- `key_hint`：用于页面确认当前凭据的末尾四位，不参与认证。
- `created_at`、`updated_at`：带时区的 UTC 时间。

公开 API 返回的服务商对象只包含 `id`、`name`、`kind`、`base_url`、`key_configured`、
`key_hint` 和时间字段。它不返回密文，也不提供读取明文密钥的接口。

内置预设即使尚未保存凭据也会出现在列表中。仓储将默认预设与持久化覆盖合并，因此用户
第一次打开页面即可选择三家服务商。

## 密钥保护

使用 `cryptography` 提供的 Fernet 认证加密，不自行实现密码算法。此依赖是持久化 Web
凭据所必需的运行时依赖。

主密钥按以下顺序解析：

1. 优先读取 `EVALHUB_CREDENTIAL_KEY`。值必须是合法 Fernet key；非法值使凭据功能
   fail-closed，并返回明确配置错误。
2. 未配置环境变量时，在 `.runtime/provider_credentials.key` 生成随机 Fernet key，创建
   权限为 `0600`。已有文件必须是普通文件、权限不得宽于 `0600`，内容必须可解析。

API Key 保存前加密，使用时只在执行进程内短暂解密。更新服务商时省略或留空 API Key
表示保留现有密钥；清除凭据必须使用明确的清除操作。密钥解密失败时不覆盖原记录，也不把
底层密文或主密钥信息写入错误消息。

本地密钥文件可以防止数据库被单独复制后直接泄密，但不能抵御已经完全控制同一用户账户
和文件系统的攻击者。需要更强运维隔离时，部署方应通过 `EVALHUB_CREDENTIAL_KEY` 注入
独立管理的主密钥。

## URL 与请求安全

- 非回环服务只允许 `https`。
- `http` 只允许 `localhost`、`127.0.0.0/8` 或 `::1`。
- Base URL 不得包含用户名、密码、查询参数或 URL fragment。
- 保存与清除密钥的 HTTP 请求只接受来自回环客户端的连接。
- 请求、日志和异常不得包含 `Authorization` header 或完整上游响应正文。
- 服务端默认继续监听 `127.0.0.1`；若用户主动绑定公网地址，凭据写接口仍保持回环限制。

## HTTP API

新增以下本地控制台接口：

- `GET /api/model-providers`：返回内置预设和自定义服务商的脱敏列表。
- `POST /api/model-providers`：创建自定义服务商；请求包含名称、Base URL 和 API Key。
- `PUT /api/model-providers/{provider_id}`：更新名称、Base URL 或 API Key。内置服务商名称
  和稳定 ID 不变。
- `DELETE /api/model-providers/{provider_id}`：删除自定义服务商；对内置服务商执行时清除
  凭据并恢复默认地址。
- `POST /api/model-providers/{provider_id}/test`：解密凭据并调用 `{base_url}/models`，返回
  排序后的模型 ID 列表和连接成功状态。

创建和更新 API 不在响应中回显传入的 API Key。模型探测只接受已持久化凭据，使密钥不会
在多个请求间反复传递。

## Web 交互

模型评测表单新增“本地 Ollama / API 服务”运行时切换，沿用当前表单的白色面板、细边框、
紧凑字段和蓝色主操作风格，不重做整个控制台视觉系统。

API 服务模式显示：

- 服务商选择器，包含三个内置预设和用户创建的自定义项。
- 当前服务地址、凭据是否已配置及末尾四位。
- “管理服务商”内联配置区，可编辑名称、Base URL 和密码字段。
- “保存并验证”操作，成功后使用 `/models` 结果填充模型选择器。
- 模型 ID 仍可手工输入，避免厂商不提供模型列表或列表暂时不可用时阻塞评测。

密码字段永不预填。留空保存保留旧密钥；清除凭据使用单独的二次确认动作。切换到 Agent
评测时表单自动回到 Ollama，并隐藏 API 服务配置。

Ollama 原有状态探测、模型下载和丰富模型选择器保持不变。API 服务不显示“已安装”、模型
大小或下载按钮。

## 评测请求与执行流程

`TaskRequest` 新增可选 `provider_id`。兼容规则如下：

- `adapter="ollama"`：`provider_id` 必须为空，保持现有行为。
- `adapter="openai-compatible"`：只允许模型评测，且 `provider_id` 必填。
- `adapter="oracle"`：只用于现有管线验证，`provider_id` 必须为空。
- Agent 请求继续只接受 `adapter="ollama"`。

任务保存 `provider_id`、模型 ID 和提交时的 Base URL 快照，用于展示和可复现信息；不保存
API Key。评测子进程按 `provider_id` 从服务商仓储读取并解密最新凭据，因此用户可以安全
轮换密钥而不修改已排队任务。服务商被删除或凭据被清除时，尚未执行的相关任务以明确的
“服务商或凭据不可用”错误失败。

## OpenAI-compatible Adapter

新增一个 `OpenAICompatibleAdapter`，继续满足现有
`ModelAdapter.generate(prompt, **kwargs) -> str` 接口。它使用非流式
`POST {base_url}/chat/completions`：

- `messages` 只包含当前样本的 `user` 文本，保持现有单轮评测语义。
- 固定 `stream: false`。
- 透传 `temperature`、`top_p` 和 `seed`。
- 将 EvalHub 的 `num_predict` 映射为 OpenAI-compatible 的 `max_tokens`。
- 从 `choices[0].message.content` 读取文本；字段缺失、类型错误或空 choices 均视为协议错误。
- 使用 `Authorization: Bearer <api_key>`，但任何异常文本不得包含该 header。

网络超时保持五分钟，与本地完整生成上限一致。HTTP 429 和 500、502、503、504 最多额外
重试两次；优先遵循数值型 `Retry-After`，否则使用短指数退避并设置上限。401、403、余额
不足类 4xx 和其他请求错误不重试。

## 首期支持矩阵

| 评测路径 | API 服务商 |
| --- | --- |
| Native Benchmark | 支持 |
| Hexagon 30 题套件 | 支持，包括其 HumanEval 节点 |
| Core `lm-eval` Chat Completions 节点 | 首期不支持，明确阻塞 |
| 需要 prompt logprobs 的节点 | 不支持，明确阻塞 |
| Core Docker HumanEval / MBPP | 首期不支持，避免向容器环境注入长期凭据 |
| Pi Agent / Coding Mini | 不支持，继续使用 Ollama |

工作流不得因为选择 API 服务商而把不支持的节点标记为成功，也不得产生虚假分数。

## 错误处理

Web 应提供可操作但脱敏的错误：

- 401：API Key 无效或已失效。
- 403：账号无权限使用目标模型。
- 429：达到服务商限流，有限重试耗尽。
- 余额或配额不足：提示检查厂商账户余额。
- 超时或连接失败：提示检查 Base URL 和网络连接。
- `/models` 不可用：允许继续手填模型 ID，但评测时仍会验证实际生成请求。
- 响应协议错误：说明服务不是兼容的 Chat Completions 接口。

持久化、解密和上游错误都要保留 Python 因果链供本地诊断，但用户消息不得包含密钥、密文、
主密钥路径内容或未截断的厂商响应。

## 测试策略

所有测试隔离真实网络和真实厂商账号：

- 密钥测试验证加密往返、错误主密钥 fail-closed、密文不含明文、自动密钥文件权限为
  `0600`。
- 仓储测试验证内置预设合并、自定义增删改、空 API Key 保留旧值和公开对象脱敏。
- Adapter 测试验证请求路径、Bearer 认证、参数映射、文本解析、401、429 重试、5xx、超时
  和畸形响应。
- Server 测试验证服务商 CRUD、回环限制、URL 校验、模型探测和任务请求中的
  `provider_id` 约束。
- 任务测试验证请求和 SQLite JSON 不含 API Key、子进程能按 ID 解析服务商、缺失凭据会
  明确失败。
- 前端测试验证运行时切换、服务商管理、密码不回显、保存并验证、模型选择、手填回退和
  Agent 强制 Ollama。
- 完整验证包含 Python pytest、Ruff、前端 Vitest、TypeScript/Vite build 和
  `git diff --check`。

## 文档与兼容性

同步更新：

- `README.md` 和本地运行指南：说明服务商配置、主密钥和 API Key 安全边界。
- `.env.example`：只添加虚拟的 `EVALHUB_CREDENTIAL_KEY` 示例，不包含真实厂商密钥。
- API 接口草案：记录服务商 CRUD、模型探测和评测请求 `provider_id`。
- 系统架构：记录服务商仓储、凭据加密和 Adapter 装配边界。

现有 Ollama、Oracle、历史任务记录和没有 `provider_id` 的 SQLite JSON 保持兼容。新增字段
必须有 `None` 默认值；旧任务恢复时不得要求服务商配置。

## 官方协议依据

- DeepSeek：<https://api-docs.deepseek.com/zh-cn/>
- 硅基流动：<https://docs.siliconflow.cn/cn/usercases/use-siliconcloud-in-OpenClaw>
- Kimi：<https://platform.kimi.ai/docs/api/overview>
