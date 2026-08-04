# EvalHub Hexagon 30 题协议实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `evalhub-hexagon-v1` 缩减为六个能力维度各 5 题、合计 30 题的可复现套件。

**Architecture:** 保持现有七节点工作流和套件 ID，裁剪固定清单并把 Hexagon 协议版本升为
`1.1.0`。运行时继续通过清单 SHA-256 和协议指纹隔离历史；排行榜只接收新的 30 题完整任务。

**Tech Stack:** Python 3.11、pytest、JSON 固定清单、SQLite 任务运行时、Markdown 文档。

## Global Constraints

- 七个来源题数固定为 `[5, 5, 5, 5, 5, 3, 2]`，六个能力维度各 5 题。
- 安全可信按样本数使用 TruthfulQA 3/5、BBQ 2/5 权重。
- 套件 ID 保持 `evalhub-hexagon-v1`，Hexagon 协议版本升为 `1.1.0`。
- 旧 60 题记录不得删除或与新 30 题排行榜混排。
- 不新增动态题数配置、不保留可切换的 60 题入口、不修改行业核心套件。
- 保护工作区已有未提交修改，只触及本计划列出的文件。

---

### Task 1: 固定 30 题 Registry 与清单合同

**Files:**
- Modify: `tests/test_benchmark_registry.py`
- Modify: `tests/test_hexagon_manifest.py`
- Modify: `src/evalhub/benchmarks/registry.py`
- Modify: `src/evalhub/datasets/hexagon_manifest.py`
- Modify: `src/evalhub/datasets/manifests/hexagon_v1.json`
- Modify: `scripts/build_hexagon_manifest.py`

**Interfaces:**
- Produces: `BenchmarkSpec.expected_sample_count` 为 `[5, 5, 5, 5, 5, 3, 2]`。
- Produces: `hexagon_manifest()` 返回 30 条、每维 5 条的固定样本。
- Produces: Hexagon 成员和套件 `version == "1.1.0"`。

- [x] **Step 1: 修改测试为新协议**

将 Registry 断言改为：

```python
assert [item.expected_sample_count for item in specs] == [5, 5, 5, 5, 5, 3, 2]
assert suite.version == "1.1.0"
assert [item.weight for item in specs[-2:]] == [0.6, 0.4]
```

将清单测试改为总量 30、各维 5，并让测试夹具只生成相同七来源计数。

- [x] **Step 2: 运行测试确认 RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_benchmark_registry.py tests/test_hexagon_manifest.py -q
```

Expected: 旧 Registry 返回 60 题、旧清单包含 60 行，因此题数和版本断言失败。

- [x] **Step 3: 实现最小 Registry 和清单变更**

在 Registry 中分别保留核心 `1.0.0` 和 Hexagon `1.1.0` 版本，设置：

```python
_HEXAGON_SAMPLE_COUNTS = (5, 5, 5, 5, 5, 3, 2)
_HEXAGON_WEIGHTS = (1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.4)
```

清单校验使用同样计数，要求 30 个唯一 ID、每种 `Capability` 恰好 5 条。裁剪现有 JSON 中
每个来源稳定顺序的前 N 条，保留原始样本内容和摘要，并把清单 `version` 改为 `1.1.0`。
构建脚本同步使用五题分层和 `1.1.0` 版本，保证以后重建不会恢复成 60 题。

- [x] **Step 4: 运行测试确认 GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_benchmark_registry.py tests/test_hexagon_manifest.py -q
```

Expected: PASS。

### Task 2: 工作流、API 与排行榜完整性

**Files:**
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_task_api.py`
- Modify: `tests/test_model_performance.py`
- Modify: `src/evalhub/tasks/performance.py`

**Interfaces:**
- Consumes: Registry 与清单的 30 题固定合同。
- Produces: 完整 Hexagon 工作流持久化 30 条样本。
- Produces: `/api/suites` 的 `expected_sample_count == 30`。
- Produces: 排行榜只接受完成数和总数均为 30 的当前协议任务。

- [x] **Step 1: 修改端到端测试为 30 题**

将工作流总数、Oracle 样本数、API 套件总数改为 30；更新新清单 SHA-256 断言。排行榜测试保留
一个 60 题旧任务，并断言只有新的 30/30 当前指纹任务进入比较范围。

- [x] **Step 2: 运行测试确认 RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow_runtime.py tests/test_task_api.py tests/test_model_performance.py -q
```

Expected: 排行榜仍要求 60 题，新的 30 题完整任务被排除。

- [x] **Step 3: 修改排行榜完整性门槛**

在 `src/evalhub/tasks/performance.py` 将 `_HEXAGON_SAMPLE_COUNT` 改为 `30`，保留现有
`sample_mode == "all"`、协议指纹和最近协议过滤逻辑。

- [x] **Step 4: 运行测试确认 GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow_runtime.py tests/test_task_api.py tests/test_model_performance.py -q
```

Expected: PASS，旧 60 题任务不进入当前排行榜。

### Task 3: 当前文档与全量验收

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `docs/getting-started/20260804_本地Benchmark评测故障排查.md`
- Modify: `docs/superpowers/specs/2026-08-04-evalhub-hexagon-benchmark-v1-design.md`

**Interfaces:**
- Documents: 当前套件为 30 次调用、每维 5 题、安全可信 3+2、协议版本 `1.1.0`。

- [x] **Step 1: 更新当前行为文档**

把正式说明中的当前 60/10 口径改为 30/5，安全可信改为 TruthfulQA 3 + BBQ 2；保留原实施计划
作为历史记录，不批量改写 `docs/superpowers/plans/` 中的旧设计过程。

- [x] **Step 2: 运行全量验证**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m ruff check . --no-cache
npm --prefix frontend run build
git diff --check
```

Expected: pytest 零失败、Ruff 零错误、前端生产构建成功、Git 无空白错误。

- [x] **Step 3: 检查文档链接与残留当前口径**

扫描 README、正式运行指南和 Hexagon 正式设计，确认当前行为不再宣称 60 题；历史计划中的
60 题记录可以保留，并明确属于旧协议。
