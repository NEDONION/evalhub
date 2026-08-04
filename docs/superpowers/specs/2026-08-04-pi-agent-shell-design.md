# EvalHub Pi Agent 壳替换设计

## 目标

把 Agent 评测的固定执行壳从 Codex CLI 完全替换为 Pi CLI，同时保持 Coding Mini
题集、隔离工作区、隐藏校验和二值评分规则不变。旧 Codex Agent 任务及运行目录全部删除，
模型评测与 Hexagon 工作流不受影响。

完成标准不是“Pi 能返回文本”，而是使用 `qwen2.5-coder:7b` 实跑简单样本时产生真实
工具调用、修改受控源码，并由隐藏校验给出可解释结果。

## 根因

现有 Codex OSS/Ollama 链路把本地模型生成的工具意图当成普通文本。多次真实运行均记录
`tool_call_count=0`、`changed_files=[]` 和 `outcome=no_action`；Qwen 输出中的
`update_plan` JSON 也没有成为 Codex 工具事件。因此统一零分来自 Agent 工具协议未接通，
不是结果聚合错误。

## 方案选择

使用 Pi 的 JSON 事件流模式，由现有 Python Worker 启动一次性 Pi 子进程。相比 RPC 模式，
它不需要维护双向命令状态机；相比 Node SDK 桥接，它不增加常驻服务。Pi 依赖放在独立的
`agent-runtime/` 目录并锁定到仍支持本机 Node 20 的 Pi `0.74.1`，不复用前端依赖，
也不修改 Python 依赖。

## 运行边界

每个 Coding Mini 样本继续使用独立 Git 工作区。`PiAgentRunner` 为该样本创建隔离配置目录，
写入仅包含当前 Ollama 地址和模型的 `models.json`，然后启动项目内
`agent-runtime/node_modules/.bin/pi`。

Pi 官方明确说明 CLI 默认继承启动用户的文件、进程和网络权限，因此不能只靠工作目录隔离。
在当前 macOS 运行环境中，整个 Pi 进程树必须由系统 `/usr/bin/sandbox-exec` 启动：允许读取
运行依赖，只允许写当前样本工作区与样本内临时目录，只允许连接配置中的本机 Ollama 端口。
沙箱程序缺失、策略加载失败或 Ollama 地址不是 loopback 时直接失败，不得静默退回无沙箱执行。

固定运行参数如下：

- JSON 事件流、无会话持久化；
- provider 为 `ollama`，API 使用 OpenAI Chat Completions 兼容端点；
- 只开放 `read`、`write`、`edit`、`bash`；
- 禁用扩展、Skills、提示模板、上下文文件发现和项目资源信任；
- 关闭更新检查、遥测与非 Ollama 网络访问；
- 当前样本工作区是唯一可写目录，超时后终止并回收整个子进程组。

Runner 从 JSONL 中提取最终助手消息、工具开始事件和安全的过程事件。现有 Benchmark 仍以
Git 差异判断是否采取行动，以隐藏 Verifier 判断实现是否通过；自然语言声明不参与评分。

## 产品与数据契约

新 Agent 请求只接受 `agent_framework="pi"`。前端类型、请求构造、表单卡片和结果详情统一
显示 `Pi CLI`；结果中的 `agent.framework` 为 `pi`，版本字段记录实际 Pi CLI 版本。

旧 Codex 数据不迁移。实施完成后，在单个 SQLite 事务内精确删除
`evaluation_type="agent"` 的任务以及关联节点、事件和样本，再删除
`.runtime/agent-runs`。模型评测任务、Hexagon 任务和数据资产不得被删除。

## 错误处理

- Pi 依赖缺失时在任务开始前返回明确安装命令，不降级到 Codex。
- macOS 沙箱不可用或策略拒绝加载时返回明确错误，不允许无保护运行。
- Ollama 模型或服务不可用时保留 Pi 的脱敏错误并把样本标为 `runtime_error`。
- JSONL 中无最终消息、进程非零退出或超时沿用 Agent Runner 错误边界。
- 工具调用为零但进程成功时保留 `no_action`，避免伪造执行成功。

## 验证

先用 fake Pi JSONL 编写回归测试，覆盖命令隔离、仅 loopback Ollama、沙箱参数、工具事件计数、
最终消息、错误与超时；再更新 API、任务执行、Coding Mini 和前端测试。完成后运行 Python
全量测试、Ruff、前端测试、前端构建和 `git diff --check`。

最后使用本机 Ollama 的 `qwen2.5-coder:7b` 真实运行至少一个简单样本。验收必须同时看到：

1. `tool_call_count > 0`；
2. `changed_files` 包含受控源码；
3. Verifier 结果与工作区最终内容一致；
4. 前端不再出现 Codex 壳标识。
