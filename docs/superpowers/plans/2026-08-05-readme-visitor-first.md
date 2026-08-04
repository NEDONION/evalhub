# README Visitor-First Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the root README into a concise GitHub project homepage that explains EvalHub, preserves all five real screenshots, and leads visitors to a working local start path.

**Architecture:** Keep the root README as the visitor-facing overview and route detailed operation, protocol, provenance, and roadmap content to existing documents under `docs/`. Use only GitHub-flavored Markdown, small centered HTML blocks, Shields badges, the existing GitHub user-attachment images, and one Mermaid overview.

**Tech Stack:** GitHub-flavored Markdown, HTML supported by GitHub README rendering, Mermaid `flowchart`, Shields.io badges.

## Global Constraints

- Keep all five current `https://github.com/user-attachments/assets/...` URLs exactly once.
- Use one screenshot as the hero and four screenshots in a two-column preview table.
- Keep the README near 200 lines and remove personal absolute paths.
- Do not add dependencies, generated assets, CI badges, release badges, or a license badge.
- Describe only implemented local MVP behavior; link detailed and changing facts to `docs/`.
- Keep Python 3.11+, Node.js 20+, npm, Ollama, and Docker prerequisites consistent with the repository.

---

### Task 1: Rewrite the visitor-facing README

**Files:**
- Modify: `README.md:1-435`
- Reference: `docs/superpowers/specs/20260804_README图解设计.md`
- Reference: `docs/getting-started/20260804_本地运行指南.md`

**Interfaces:**
- Consumes: the five existing GitHub user-attachment URLs and the current verified local run commands.
- Produces: a standalone GitHub README with stable anchors for `#features`, `#quick-start`, `#screenshots`, `#how-it-works`, `#benchmarks`, `#documentation`, and `#contributing`.

- [x] **Step 1: Replace the opening with a compact GitHub project header**

Use a centered title, a one-line value proposition, truthful Python/React/local-first badges, and anchor links. Place the existing `f94085b8-dbe6-423c-8e12-83b38ff0ad2b` screenshot after the opening copy with alt text `EvalHub model evaluation setup`.

- [x] **Step 2: Replace the long capability list with four visitor-oriented capabilities**

Create `## Features` with a four-row table covering model Benchmark execution, fixed-shell Agent evaluation, reproducible persistent workflows, and the six-dimension capability profile. Do not list planned PostgreSQL, Celery, RabbitMQ, or MinIO infrastructure.

- [x] **Step 3: Move the portable local start path near the top**

Create `## Quick Start` with this exact primary flow:

```bash
git clone https://github.com/NEDONION/evalhub.git
cd evalhub
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
npm --prefix frontend install
./scripts/start_local.sh
```

State Python 3.11+, Node.js 20+, and npm as prerequisites. Explain in one sentence that Ollama provides local inference and Docker enables code Benchmarks, then link the local run guide.

- [x] **Step 4: Preserve all remaining screenshots in a labeled gallery**

Create `## Screenshots` with a two-column HTML table. Preserve these four URLs once each:

- `360899cf-270d-4133-9c40-f48097ceaa3d`: model task DAG and resource metrics.
- `5e1c29b4-4b31-4b09-8a02-dd6d3f8e8542`: Agent live process and failed-sample audit.
- `36fd8ac4-2ee5-44e1-9ee6-dbb34b897293`: Agent six-dimension result profile.
- `b56c2a2d-503d-4d30-a1ca-2016e5200f83`: model and dataset asset management.

Give every image descriptive alt text and a short visible title. Do not crop, copy, regenerate, or replace the screenshots.

- [x] **Step 5: Reduce architecture and benchmark detail to visitor summaries**

Create `## How It Works` with one Mermaid `flowchart` from Web/CLI through the evaluation engine and persistent workflow to scores and the capability profile. Create `## Benchmarks` with a compact table for individual public Benchmarks, the 30-call Hexagon Mini Suite, and fixed-shell Coding Mini Agent evaluation.

State the three important limitations: the Mini Suite is not a full upstream leaderboard score; code evaluation needs Docker; prompt-logprob protocols block on the current Ollama adapter instead of receiving approximate scores.

- [x] **Step 6: End with documentation, project status, and contribution paths**

Link the documentation center, local run guide, system architecture, Agent roadmap, and Hexagon retrospective. Add a `Local MVP` status note and a short contribution section with these checks:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
```

Do not add a License section until the repository has a maintainer-selected `LICENSE` file.

### Task 2: Validate the GitHub README contract

**Files:**
- Verify: `README.md`
- Verify: `docs/superpowers/specs/20260804_README图解设计.md`
- Verify: `docs/superpowers/plans/2026-08-05-readme-visitor-first.md`

**Interfaces:**
- Consumes: the rewritten Markdown and existing repository files.
- Produces: evidence that the README preserves assets, uses valid local links, and does not affect project behavior.

- [x] **Step 1: Check structural invariants**

Run:

```bash
rg -n "user-attachments/assets/" README.md
rg -n '^```mermaid$' README.md
rg -n '/Users/nedonion|TODO|TBD' README.md
wc -l README.md
```

Expected: five unique attachment lines, one Mermaid opening fence, no personal path or placeholder matches, and approximately 200 lines.

- [x] **Step 2: Check every relative Markdown link target**

Run a read-only `.venv/bin/python` check that extracts non-anchor, non-HTTP Markdown links from `README.md`, URL-decodes them, and asserts each target exists relative to the repository root. Expected: exit code 0 and no missing path output.

- [x] **Step 3: Run repository checks required by `AGENTS.md`**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
git diff --check
```

Expected: Ruff passes, all pytest tests pass, and Git reports no whitespace errors.

- [x] **Step 4: Review the final diff for scope and factual accuracy**

Run:

```bash
git status --short
git diff -- README.md docs/superpowers/specs/20260804_README图解设计.md docs/superpowers/plans/2026-08-05-readme-visitor-first.md
```

Expected: only the README design, implementation plan, and README are changed; no generated assets, secrets, source code, or unrelated files appear.
