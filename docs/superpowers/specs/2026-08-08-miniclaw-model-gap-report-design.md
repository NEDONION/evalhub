# MiniClaw 弱模型差距实验与 README 报告设计

## 目标

在冻结的 `coding-mini-v3` 上，把历史 Pi 基线为 1/6 的本地模型 `qwen3:4b`
接入完整 MiniClaw Agent，运行全部 6 道题，并把所有可审计的 MiniClaw 正式结果写入
README 的独立章节。实验用于观察同一模型更换 Agent 后的差异，不把协议或基础设施故障
伪装成能力分。

## 候选选择

当前 `coding-mini-v3` 固定矩阵没有恰好 2/6 的模型。`qwen3:4b` 与 `qwen3:14b`
均为 1/6；选择 `qwen3:4b`，因为它已经安装在本机、运行成本更低，且与 Pi 基线使用
完全相同的模型 ID、题集版本和脚手架。旧任务中出现过 2/6，但题集版本不同，不进入
本轮严格对照。

## 实验边界

- MiniClaw 版本、身份、记忆、Skills、工具、权限和审批策略保持不变。
- 只在当前 EvalHub 服务进程中临时覆盖模型为 `qwen3:4b`，Provider 地址为
  `http://127.0.0.1:11434/v1`。
- Ollama 的 OpenAI-compatible 端点不需要真实凭据；MiniClaw 所需的非空 Key 使用
  仅存在于进程内的固定本地占位值，不写入 `.env`、配置、任务或结果。
- `write_file` 和 `edit_file` 仍只允许在 EvalHub 当前样本工作区内一次性批准；命令、网络、
  记忆和工作区外操作不扩大权限。
- 正式任务固定使用 `agent_framework=miniclaw`、`sample_mode=all` 和
  `agent_difficulty=all`，不修改 6 道题、隐藏 Verifier 或超时。
- 实验结束后停止临时服务，恢复默认 `deepseek-v4-pro` MiniClaw 服务；正式结果继续保存在
  EvalHub 主运行库中。

## 结果有效性

首个没有基础设施故障、并完成全部 6 个样本的正式任务作为 qwen3:4b 结果，不按得分重跑或
挑选最佳值。任务状态、协议预检、工具事实、工作区变化和隐藏 Verifier 分开记录：

- 隐藏 Verifier 决定 6 题通过数和六维能力分。
- 协议预检是诊断证据，不直接计入正式通过率。
- Provider、桥或本地服务故障单列为排除运行，不进入能力排名。
- 若正式任务包含单次上游协议异常，保留原始得分，并在注释中说明；定向复测只能用于分类，
  不能替换正式任务成绩。

## README 章节

在现有 `Agent Benchmark Report` 后新增顶层章节
`MiniClaw Agent Evaluation Report`，同时在页首导航增加入口。章节包含：

1. 正式结果表：模型、运行方式、Job ID、协议预检、通过数、失败样本、工具调用、工具错误和
   平均每题耗时。
2. 六维能力表：规划、代码理解、实现正确性、工具使用、验证能力和稳健性。
3. 同模型 Pi vs MiniClaw 表：至少覆盖 Flash 与 qwen3:4b，显示通过数、工具调用和平均耗时，
   Pro 也在数据可比时展示。
4. 排除运行表：记录 Flash Provider 解析修复前的失败任务及排除原因，不混入排名。
5. 可复现性说明：固定 `coding-mini-v3`、脚手架哈希、MiniClaw 版本、单次正式运行原则，
   并声明 6 题诊断集不是官方 SWE-bench 排名。

已存在的正式数据包括 MiniClaw + DeepSeek V4 Pro 6/6，以及 MiniClaw +
DeepSeek V4 Flash 5/6；qwen3:4b 的单次正式结果只能在本轮任务结束后写入，不预填结论。

## 验证

- 启动前确认 Ollama 已安装 `qwen3:4b`，OpenAI-compatible 端点可用。
- 通过 `/api/agents` 确认临时 MiniClaw 实际报告 `qwen3:4b` 后再提交任务。
- 等待任务终态并从持久化 API 提取公开聚合字段，不复制模型正文或敏感数据。
- 恢复服务后确认 `/api/agents` 再次报告默认 Pro，同时 qwen 任务仍可读取。
- README 修改后运行 `git diff --check`，并按仓库完成定义运行 Ruff、pytest 与相关前端检查。
- MiniClaw Provider 的兼容修复继续运行 Provider 单测与 Ruff；仓库中的其他未提交修改不纳入
  本任务提交。
