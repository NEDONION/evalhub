# Agent Benchmark v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Agent 评测升级为可解释的 Coding Mini v3，补齐协议预检和过程指标，并在根 README 发布固定 10 模型对比；真实 SWE-bench Verified 保持独立 6 题套件。

**Architecture:** 保留固定 Pi Agent 壳和现有任务 DAG。轻量题继续由 `coding_mini.py` 创建隔离 Git 工作区并运行隐藏校验；过程事实由 Pi 标准化事件和 Git 差异一次采集、统一聚合。真实 SWE-bench 只保存固定官方清单并调用官方 Docker Harness，不新建通用沙箱层。

**Tech Stack:** Python 3.11、pytest、现有 Pi CLI、Git、React/TypeScript、原生 SVG、SWE-bench 官方 Docker Harness。

## Global Constraints

- 不新增 Agent 框架、图表库、容器 SDK 或通用沙箱抽象。
- API Key 只留在既有加密 Provider 仓储和本机代理，结果与 README 不记录凭据。
- v2 历史结果保持可读；新运行固定写入 `coding-mini-v3`。
- 先运行最相关测试，再运行全量 Ruff、pytest、前端测试与构建。
- 当前工作区已有本轮 API Agent 与评分修复，必须在原改动上增量实现，不回退。

---

## File Map

- Modify: `src/evalhub/benchmarks/coding_mini.py` — Mini v3 样本、协议预检、样本诊断和聚合指标。
- Modify: `src/evalhub/agent/pi.py` — 保持事件规范化并消除当前重复代码，不扩大公开接口。
- Modify: `tests/test_coding_mini.py` — v3 gold 实现、协议状态和过程指标回归测试。
- Modify: `tests/test_pi_agent.py` — 工具错误事件计数边界。
- Modify: `frontend/src/types.ts` — 过程指标与协议预检结果类型。
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.tsx` — 紧凑过程指标和逐题表格。
- Modify: `frontend/src/App.test.tsx` — 任务详情展示契约。
- Create: `src/evalhub/benchmarks/swebench_verified_mini.py` — 固定 6 个官方实例与官方 Harness 就绪检查。
- Create: `tests/test_swebench_verified_mini.py` — 清单冻结、命令和阻塞语义测试。
- Modify: `src/evalhub/server.py`、`src/evalhub/tasks/executor.py` — 仅在官方 gold 就绪时开放独立真实套件。
- Modify: `docs/architecture/20260804_API接口草案.md`、`docs/product/20260804_Agent评测路线图.md`、`docs/getting-started/20260804_本地运行指南.md` — 已验证行为与运行边界。
- Create: `docs/assets/agent-model-comparison.svg` — 10 模型 Agent 六维小多图。
- Modify: `README.md` — 两套 Agent 口径、10 模型榜单、协议和过程指标。

## Task 1: Stabilize the Current Agent Runner Contract

- [ ] 在 `tests/test_coding_mini.py` 增加失败断言：Runner 抛错后仍保留已观察到的工具次数、工具错误、真实耗时和文件变化。
- [ ] 在 `tests/test_pi_agent.py` 增加失败断言：`tool_finished.payload.is_error=true` 可由上游稳定识别。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_pi_agent.py tests/test_coding_mini.py -q`，确认新断言先失败。
- [ ] 在 `coding_mini.py` 的单样本边界用 `monotonic()` 和事件回调计数，不修改 Pi 的工具执行语义。
- [ ] 修复当前重复参数、重复分支和重复配置项，使模块重新可导入。
- [ ] 再运行同一测试，确认通过。

## Task 2: Add Protocol Preflight and Aggregate Execution Metrics

- [ ] 在 `tests/test_coding_mini.py` 增加 compatible、degraded、incompatible 三种预检 Fake，以及 `protocol_error` 分类测试。
- [ ] 增加 `execution_summary` 精确断言，覆盖总/平均工具次数、错误、耗时、改动文件和五类 outcome。
- [ ] 运行单测确认失败。
- [ ] 在正式 6 题前创建一次独立 marker 工作区，要求结构化工具写入精确 `OK\n`；预检不计入通过率。
- [ ] 将预检状态传给样本分类：不兼容且无动作才是 `protocol_error`，其余保持 verifier 优先。
- [ ] 聚合每题 `diagnostics` 为 `execution_summary`，旧结果字段保持不变。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_coding_mini.py -q`。

## Task 3: Replace the Daily Suite with Coding Mini v3

- [ ] 先把测试期望样本固定为：`path_normalization`、`config_precedence`、`pagination_merge`、`cache_expiry`、`reservation_idempotency`、`async_worker_cleanup`。
- [ ] 扩充测试 Fake 的六个 gold 修复，确保完整运行 6/6；每档跳过一题时只降低该题权重覆盖的维度。
- [ ] 为路径逃逸、输入不变、重复游标、注入时钟、幂等累计、异步清理各保留至少一个隐藏边界断言。
- [ ] 运行测试确认旧 v2 实现失败。
- [ ] 用六个自包含 Python 工作区替换样本定义，版本改为 `coding-mini-v3`，不增加第三方依赖。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_coding_mini.py tests/test_task_executor.py tests/test_task_api.py -q`。

## Task 4: Show Process Metrics in the Task Drawer

- [ ] 在 `frontend/src/App.test.tsx` 的 Agent fixture 加入预检、聚合和逐题诊断，并断言“执行过程指标”、工具次数、平均耗时和失败类型可见。
- [ ] 运行 `npm --prefix frontend run test:run -- App.test.tsx`，确认失败。
- [ ] 在 `frontend/src/types.ts` 增加向后兼容的可选类型字段。
- [ ] 在 `EvaluationResultDetail.tsx` 用现有卡片和表格样式展示聚合指标与逐题诊断；没有新字段的 v2 结果不显示该区。
- [ ] 运行前端定向测试与 `npm --prefix frontend run build`。

## Task 5: Freeze the Six-Task SWE-bench Verified Suite

- [ ] 在 `tests/test_swebench_verified_mini.py` 固定 6 个官方 instance ID、数据集名、套件版本和 SHA-256 清单指纹。
- [ ] 用注入的命令执行器测试 Docker 不可用、官方 Harness 不可用、gold 非 6/6 时均返回 `executor_not_ready`，不产生成绩。
- [ ] 运行测试确认失败。
- [ ] 在 `swebench_verified_mini.py` 实现最小清单和官方 Harness 命令构造；不复制 SWE-bench 测试逻辑。
- [ ] 只有本机 gold 6/6 证明写入就绪标记后，API/执行器才接受 `swebench_verified_mini`；否则清晰阻塞并给出官方准备命令。
- [ ] 运行相关后端测试；随后在 Docker Desktop 上实际运行官方 gold 验证，并记录真实结果。

## Task 6: Publish the Fixed 10-Model Agent Report

- [ ] 在同一 `coding-mini-v3` 脚手架和 Pi 版本上依次运行 4 个 API 与 6 个本地模型；保存任务 ID、Provider、预检状态、通过率、六维分数、工具次数和耗时。
- [ ] 若某个 Provider 或模型不可用，保留“未完成”而不是伪造 0 分；不混用旧 v2 成绩补位。
- [ ] 生成一个原生、闭合多边形的 `docs/assets/agent-model-comparison.svg`，10 个小图共用 0–100 刻度。
- [ ] 在根 README 增加 Agent Report 独立榜单和可点击原图；注明 6 题诊断集、版本、日期、Pi CLI 与协议组。
- [ ] SWE-bench 6 题全部完成后再发布其独立榜单，不能与 Mini v3 合并排名。

## Task 7: Documentation, Review, and Full Verification

- [ ] 同步 API 草案、Agent 路线图和本地运行指南，只写已实现且验证过的行为。
- [ ] 运行 Ponytail review，删除无必要抽象、依赖和重复兼容层。
- [ ] 运行 `.venv/bin/python -m ruff check .`。
- [ ] 运行 `.venv/bin/python -m pytest`。
- [ ] 运行 `npm --prefix frontend run test:run` 与 `npm --prefix frontend run build`。
- [ ] 运行 `git diff --check`，检查 diff 无密钥、日志和意外产物。
- [ ] 只暂存本任务文件，提交并按用户要求推送 `main`。

## Plan Self-Review

- 每个需求均映射到现有执行边界；没有新建通用 Runner、Registry、图表包或容器 SDK。
- Mini v3、SWE-bench 和 README 榜单使用明确版本，旧 v2 不会被静默混入。
- 所有外部依赖步骤都有阻塞语义，不把 Docker/Provider 故障记作模型零分。
- 测试先覆盖可观察行为，再修改实现；文件名、字段名和 10 模型清单与已确认设计一致。
