# MiniClaw README Compact Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Job ID column from the MiniClaw formal-results table without changing model names, other columns, or evaluation data.

**Architecture:** Make one localized Markdown table edit in `README.md`. Preserve the excluded-runs Job ID column because it is outside the formal-results table and remains useful for audit evidence.

**Tech Stack:** Markdown, Git.

## Global Constraints

- Delete only the formal-results table's `Job ID` column and its three values.
- Keep complete model names and every other field unchanged.
- Do not alter evaluation scores, explanatory prose, the six-dimension table, the Pi comparison, or the excluded-runs table.

---

### Task 1: Remove the formal-results Job ID column

**Files:**
- Modify: `README.md:226-230`

**Interfaces:**
- Consumes: the existing three-row MiniClaw formal-results Markdown table.
- Produces: the same table with seven columns instead of eight.

- [ ] **Step 1: Confirm the current table still contains the target column**

Run:

```bash
sed -n '226,231p' README.md
```

Expected: the formal-results header contains `Job ID`, and each model row contains one `job_...` cell.

- [ ] **Step 2: Apply the minimal Markdown edit**

Replace only the formal-results table with:

```markdown
| 模型 | 运行方式 | 协议预检 | 通过样例 | 失败样例 | 工具调用 / 错误 | 平均耗时/题 |
| --- | --- | --- | ---: | --- | ---: | ---: |
| `deepseek-v4-pro` | DeepSeek API | compatible | **6 / 6** | — | 37 / 0 | 45.21 s |
| `deepseek-ai/DeepSeek-V4-Flash` | SiliconFlow API | incompatible（60 s 预检超时） | **5 / 6** | `async_worker_cleanup` | 40 / 0 | 46.03 s |
| `qwen3:4b` | Ollama | compatible | **0 / 6** | 全部 6 题 | 2 / 0 | 100.55 s |
```

- [ ] **Step 3: Verify scope and Markdown consistency**

Run:

```bash
sed -n '218,270p' README.md
git diff -- README.md
git diff --check
```

Expected: the formal table has seven cells per row, its Job IDs are gone, the excluded-runs table still has its own Job ID, and the diff has no whitespace errors.

- [ ] **Step 4: Commit the README edit**

```bash
git add README.md
git commit -m "docs: 精简 MiniClaw 正式结果表"
```
