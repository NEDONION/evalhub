# README Hexagon Benchmark Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the completed 11-model EvalHub Hexagon v1.2.0 benchmark as a full root-README report with comparable six-dimension small-multiple charts.

**Architecture:** Keep the report as a static, auditable snapshot. `README.md` owns methodology, exact tables, interpretation, and caveats; one standalone SVG owns all 11 radar panels and is referenced with a relative GitHub path.

**Tech Stack:** GitHub-flavored Markdown, SVG 1.1, Python standard library only for verification, existing Ruff and pytest checks.

## Global Constraints

- Modify only `README.md` and create `docs/assets/hexagon-model-comparison.svg` for the report deliverable.
- Do not add a runtime dependency, report generator, frontend behavior, or CI automation.
- Use `evalhub-hexagon-v1` version `1.2.0`, run date `2026-08-05`, 11 successful tasks, and 330 evaluated samples.
- Describe the results as a 30-sample local capability suite, not as full upstream Benchmark leaderboard scores.
- Separate ordinary Ollama, Ollama with `think=false`, and OpenAI-compatible comparison fingerprints in the methodology note.

---

### Task 1: Create the six-dimension small-multiple SVG

**Files:**
- Create: `docs/assets/hexagon-model-comparison.svg`

**Interfaces:**
- Consumes: the frozen score rows below, with axis order `knowledge`, `instruction_following`, `mathematics`, `reasoning`, `coding`, `safety_trust`.
- Produces: a self-contained SVG referenced by `README.md` as `docs/assets/hexagon-model-comparison.svg`.

Frozen rows, in report ranking order:

```text
deepseek-v4-pro      83.33 | 60 100  80 80 80 100
gemma4:12b           70.00 | 60 100 100 40 20 100
granite4.1:3b        66.67 | 20 100  60 80 40 100
qwen3:14b            66.67 | 40 100 100 40 20 100
deepseek-v4-flash    66.67 | 80 100   0 40 100 80
granite3.3:8b        53.33 | 60 100  80  0  0 80
qwen2.5-coder:7b     50.00 | 40  80  60 20 40 60
qwen2.5:1.5b         40.00 | 40  60  60 20  0 60
deepseek-r1:1.5b     33.33 | 20  40  60  0 20 60
qwen2.5:0.5b         26.67 | 20  20  40 20  0 60
qwen3:4b             20.00 | 20  40  20  0  0 40
```

- [ ] **Step 1: Add the static SVG**

Create a 3-column by 4-row SVG. Each panel must use the same center, 100-point outer hexagon, 25/50/75 guide rings, six spokes, label positions, blue result polygon, model name, and overall score. Use a white canvas, `#0f172a` text, `#cbd5e1` grids, `#2563eb` stroke, and a translucent blue fill.

- [ ] **Step 2: Validate SVG structure**

Run:

```bash
xmllint --noout docs/assets/hexagon-model-comparison.svg
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Verify panel and polygon counts**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; import xml.etree.ElementTree as ET; p=Path("docs/assets/hexagon-model-comparison.svg"); root=ET.parse(p).getroot(); text=p.read_text(encoding="utf-8"); assert root.tag.endswith("svg"); assert text.count("class=\"model-panel\"")==11; assert text.count("class=\"score-polygon\"")==11'
```

Expected: exit code 0 and no output.

- [ ] **Step 4: Render and inspect the chart**

Run:

```bash
qlmanage -t -s 1800 -o /tmp docs/assets/hexagon-model-comparison.svg
```

Expected: `/tmp/hexagon-model-comparison.svg.png` renders all 11 closed polygons without clipping or disconnected edges.

### Task 2: Add the complete report to the root README

**Files:**
- Modify: `README.md`, immediately after the `Screenshots` section and before `How It Works`.

**Interfaces:**
- Consumes: `docs/assets/hexagon-model-comparison.svg` and the same frozen rows from Task 1.
- Produces: a GitHub-visible `Model Benchmark Report` section with exact scores and reproducibility context.

- [ ] **Step 1: Insert methodology and overall ranking**

Add a section containing the run date, Suite ID/version, 30 samples per model, 330 total evaluated samples, 11/11 successful tasks, and a ranking table with model, runtime (`Ollama` or `DeepSeek API`), passed samples, and average score.

- [ ] **Step 2: Insert the six-dimension table and SVG**

Add one 11-row table with the six frozen dimension scores, then reference:

```html
<img alt="EvalHub Hexagon comparison of eleven local and DeepSeek models" src="docs/assets/hexagon-model-comparison.svg" />
```

- [ ] **Step 3: Add interpretation and comparability notes**

State only observations supported by the rows: DeepSeek Pro leads overall; DeepSeek Flash leads coding but scores zero on this run's GSM8K slice; Gemma leads local overall; instruction following saturates for several models; 5-sample dimensions change in 20-point steps. Explain the three comparison fingerprints without printing full hashes.

- [ ] **Step 4: Verify README references and coverage**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; text=Path("README.md").read_text(encoding="utf-8"); assert "## Model Benchmark Report" in text; assert "docs/assets/hexagon-model-comparison.svg" in text; models=("deepseek-v4-pro","gemma4:12b","granite4.1:3b","qwen3:14b","deepseek-v4-flash","granite3.3:8b","qwen2.5-coder:7b","qwen2.5:1.5b","deepseek-r1:1.5b","qwen2.5:0.5b","qwen3:4b"); assert all(model in text for model in models)'
```

Expected: exit code 0 and no output.

### Task 3: Verify and publish the report

**Files:**
- Verify: `README.md`
- Verify: `docs/assets/hexagon-model-comparison.svg`

**Interfaces:**
- Consumes: the complete report from Tasks 1–2.
- Produces: one reviewed commit on `main`, pushed to `origin/main`.

- [ ] **Step 1: Run repository checks**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
git diff --check
```

Expected: Ruff passes, pytest passes, and `git diff --check` has no output.

- [ ] **Step 2: Review the exact diff**

Run:

```bash
git status --short
git diff -- README.md docs/assets/hexagon-model-comparison.svg
```

Expected: only the two report deliverables are uncommitted; no credentials, model responses, caches, or runtime data appear.

- [ ] **Step 3: Commit and push**

Run:

```bash
git add README.md docs/assets/hexagon-model-comparison.svg
git commit -m "docs: publish hexagon model benchmark report"
git push origin main
```

Expected: the commit succeeds and `origin/main` advances to the new report commit.
