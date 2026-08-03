# EvalHub Full Python Chinese Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add accurate Chinese documentation and dense explanatory comments to all 36 current Python files, preserve authorized working-tree features, make Ruff and pytest green, and push the result to remote `main` without force.

**Architecture:** First preserve the two remaining authorized Python working-tree paths in one focused commit, then fast-forward an isolated feature worktree to that baseline. The server/frontend-directory pair is already tracked in commit `89d9616`. Annotate the repository in four reviewable module groups, verify each group, run a one-time AST/tokenize compliance checker, merge to local `main`, and push normally to `origin/main`.

**Tech Stack:** Python 3.11+, standard-library `ast` and `tokenize`, Ruff, pytest, Git.

## Global Constraints

- Scope is exactly `src/evalhub/**/*.py`, `tests/**/*.py`, and `run_evalhub.py` as they exist at the execution baseline.
- Every Python module and class receives a detailed Chinese docstring.
- Every `def` and `async def`, including private, test, protocol, abstract, and dunder methods, receives a detailed Chinese docstring.
- Function and method bodies may not contain more than 5 consecutive logical code lines without a detailed Chinese comment.
- Comments explain purpose, business meaning, constraints, state changes, or failure handling; filler comments are forbidden.
- Multiline expressions, decorators, signatures, strings, and chained calls remain intact.
- Business behavior remains unchanged except for the four user-authorized baseline paths already present in the working tree.
- Ruff fixes are limited to import ordering, equivalent line wrapping, and precise documented suppressions where behavior requires them.
- Do not stage or commit unrelated frontend, documentation-restructure, shell, or other working-tree changes.
- Use ordinary Git push only; never force push.

---

## File Structure

### Domain, engine, and registry group

- Modify: `src/evalhub/domain/__init__.py`
- Modify: `src/evalhub/domain/enums.py`
- Modify: `src/evalhub/domain/entities.py`
- Modify: `src/evalhub/domain/repositories.py`
- Modify: `src/evalhub/engine/__init__.py`
- Modify: `src/evalhub/engine/reports.py`
- Modify: `src/evalhub/engine/runner.py`
- Modify: `src/evalhub/registry/__init__.py`
- Modify: `src/evalhub/registry/in_memory.py`

### Adapter and evaluator group

- Modify: `src/evalhub/adapters/__init__.py`
- Modify: `src/evalhub/adapters/base.py`
- Modify: `src/evalhub/adapters/local.py`
- Modify: `src/evalhub/adapters/ollama.py`
- Modify: `src/evalhub/evaluators/__init__.py`
- Modify: `src/evalhub/evaluators/base.py`
- Modify: `src/evalhub/evaluators/exact_match.py`
- Modify: `src/evalhub/evaluators/numeric.py`
- Modify: `src/evalhub/evaluators/choice.py`
- Modify: `src/evalhub/evaluators/registry.py`

### Dataset and application group

- Modify: `src/evalhub/__init__.py`
- Modify: `src/evalhub/datasets/__init__.py`
- Modify: `src/evalhub/datasets/catalog.py`
- Modify: `src/evalhub/datasets/loaders.py`
- Modify: `src/evalhub/config.py`
- Modify: `src/evalhub/api/__init__.py`
- Modify: `src/evalhub/api/main.py`
- Modify: `src/evalhub/ollama.py`
- Modify: `src/evalhub/cli.py`
- Modify: `src/evalhub/server.py`
- Modify: `run_evalhub.py`

### Test group

- Modify: `tests/test_cli_parser.py`
- Modify: `tests/test_exact_match.py`
- Modify: `tests/test_ollama_adapter.py`
- Modify: `tests/test_ollama_status.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_server_frontend.py`

## Canonical Documentation Form

Use Google-style sections with Chinese prose, omitting sections that do not apply:

```python
def load_samples(name: str, *, limit: int | None = None) -> list[EvaluationSample]:
    """加载并转换指定数据集的评测样本。

    Args:
        name: 数据集目录中注册的稳定名称。
        limit: 最多返回的样本数；为 ``None`` 时返回全部样本。

    Returns:
        可直接传给评测引擎的领域样本列表。

    Raises:
        KeyError: 数据集名称未在目录中注册。
    """
    # 先解析目录元数据，确保后续加载逻辑使用统一的数据集定义。
    spec = get_dataset_spec(name)
```

For short protocol or abstract methods, expand the body without changing semantics:

```python
def get(self, model_id: str) -> ModelRecord:
    """按稳定标识读取模型记录，未命中时由实现抛出 ``KeyError``。"""
    ...
```

### Task 1: Preserve the authorized Python baseline

**Files:**
- Commit as-is: `src/evalhub/ollama.py`
- Commit as-is: `tests/test_ollama_status.py`
- Already tracked in `89d9616`: `src/evalhub/server.py`
- Already tracked in `89d9616`: `tests/test_server_frontend.py`

**Interfaces:**
- Consumes: the user-authorized Python working-tree state in the main checkout.
- Produces: one focused Ollama commit that the isolated feature worktree can fast-forward to before annotation begins.

- [ ] **Step 1: Verify the authorized Ollama changes**

Run in `/Users/nedonion/PycharmProjects/evalhub`:

```bash
.venv/bin/python -m pytest tests/test_ollama_status.py -q
git diff -- src/evalhub/ollama.py tests/test_ollama_status.py
```

Expected: four Ollama status tests pass; the diff contains recommended model options and their regression test.

- [ ] **Step 2: Commit only the Ollama pair**

```bash
git add src/evalhub/ollama.py tests/test_ollama_status.py
git commit --only src/evalhub/ollama.py tests/test_ollama_status.py -m "feat: expose recommended Ollama models"
```

Expected: the commit contains exactly the two named files; unrelated index entries remain untouched.

- [ ] **Step 3: Fast-forward the isolated branch to the preserved baseline**

Run in the isolated worktree:

```bash
mkdir -p .venv
ln -s /Users/nedonion/PycharmProjects/evalhub/.venv/bin .venv/bin
test -x .venv/bin/python
git merge --ff-only main
.venv/bin/python -m pytest
```

Expected: the ignored worktree-local environment link is executable, the feature branch includes the Ollama baseline commit plus existing server commit `89d9616`, and all 13 tests pass.

### Task 2: Annotate domain, engine, and in-memory registry

**Files:**
- Modify: all nine files in the domain, engine, and registry group.
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: domain entities, repository protocols, model/evaluator abstractions, and existing runner behavior.
- Produces: documented lifecycle states, repository contracts, report aggregation, and in-memory storage behavior.

- [ ] **Step 1: Add module, enum, entity, and protocol documentation**

Apply the canonical form with these exact semantic targets:

- `domain/enums.py`: explain model source types and evaluation job lifecycle states.
- `domain/entities.py`: explain identifier generation, UTC timestamps, every dataclass role, and job transition methods.
- `domain/repositories.py`: explain each protocol's persistence contract and missing-ID behavior without changing signatures.
- `domain/__init__.py`: explain that it is the stable public import surface for domain types.

Place detailed Chinese comments before ID creation, timestamp capture, and each state transition. Keep every dataclass field and default unchanged.

- [ ] **Step 2: Add engine and report documentation**

Document `build_report()` with arguments, empty-result behavior, aggregation semantics, and return value. Document `EvaluationRunner` construction and `run()` with adapter/evaluator orchestration, runtime-config precedence, job state transitions, result creation, and exception propagation. Add Chinese comments before configuration merge, sample iteration, result aggregation, success transition, and failure transition.

- [ ] **Step 3: Add registry documentation and retain generic ID lookup behavior**

Document `_Table`, `_JobTable`, `_ResultTable`, and `InMemoryRegistry`, including `KeyError` behavior and insertion order. Keep runtime attribute lookup for generic entities and use a precise documented suppression if Ruff reports `B009`:

```python
# 泛型表接收多种领域实体，只能在运行时通过共同的 ``id`` 约定取得主键。
item_id = getattr(item, "id")  # noqa: B009
```

- [ ] **Step 4: Verify and commit the group**

```bash
.venv/bin/python -m pytest tests/test_runner.py -q
.venv/bin/python -m ruff check src/evalhub/domain src/evalhub/engine src/evalhub/registry
git diff --check -- src/evalhub/domain src/evalhub/engine src/evalhub/registry
git add src/evalhub/domain src/evalhub/engine src/evalhub/registry
git commit -m "docs: explain domain engine and registry code"
```

Expected: runner tests pass, Ruff reports no errors for the group, and the commit contains only the nine group files.

### Task 3: Annotate model adapters and evaluator plugins

**Files:**
- Modify: all ten files in the adapter and evaluator group.
- Test: `tests/test_ollama_adapter.py`
- Test: `tests/test_exact_match.py`

**Interfaces:**
- Consumes: `ModelAdapter`, `Evaluator`, metric entities, and the evaluator registry.
- Produces: documented extension contracts, HTTP error handling, normalization rules, and plugin registration behavior.

- [ ] **Step 1: Document adapter contracts and implementations**

Document the adapter package export surface, abstract `ModelAdapter.generate()`, deterministic `StaticMappingAdapter`, and `OllamaAdapter`. For Ollama generation, explain payload construction, supported runtime options, HTTP request boundaries, response decoding, malformed-response errors, and connection-error translation. Add comments at each network/error boundary without logging prompts or secrets.

- [ ] **Step 2: Document evaluator contracts and algorithms**

Document the evaluator package exports, abstract `Evaluator.evaluate()`, exact-match normalization, numeric extraction, choice-letter extraction, registry registration/creation, and default registry construction. Explain regex intent and comparison semantics. Reorder `numeric.py` imports and equivalently wrap the long unknown-evaluator error in `registry.py`.

- [ ] **Step 3: Verify and commit the group**

```bash
.venv/bin/python -m pytest tests/test_ollama_adapter.py tests/test_exact_match.py -q
.venv/bin/python -m ruff check src/evalhub/adapters src/evalhub/evaluators tests/test_ollama_adapter.py tests/test_exact_match.py
git diff --check -- src/evalhub/adapters src/evalhub/evaluators
git add src/evalhub/adapters src/evalhub/evaluators
git commit -m "docs: explain adapters and evaluator plugins"
```

Expected: four targeted tests pass and Ruff reports no errors for the implementation group.

### Task 4: Annotate datasets, configuration, entry points, CLI, Ollama status, and server

**Files:**
- Modify: all eleven files in the dataset and application group.
- Test: `tests/test_cli_parser.py`
- Test: `tests/test_ollama_status.py`
- Test: `tests/test_server_frontend.py`

**Interfaces:**
- Consumes: dataset catalog definitions, local filesystem/network boundaries, CLI arguments, Ollama status API, and HTTP request payloads.
- Produces: documented data preparation, application configuration, user commands, model discovery, static frontend selection, and JSON API behavior.

- [ ] **Step 1: Document package, catalog, loader, config, and API modules**

Document public package exports and these behaviors:

- catalog stability, supported datasets, and unknown-name errors;
- GSM8K/MMLU download locations, safe archive extraction, answer parsing, subject selection, and limit semantics;
- environment-based configuration defaults;
- optional FastAPI import boundary and health response.

Reorder `datasets/loaders.py` imports with Ruff. Add comments before filesystem creation, downloads, security checks, external-format conversion, and truncation.

- [ ] **Step 2: Document Ollama status discovery**

Document recommended-model metadata, command discovery, status response schema, installed/recommended option merging, and recommendation lookup. Add comments before command checks, HTTP status calls, response normalization, installed-model prioritization, and fallback recommendation insertion. Reorder imports without changing behavior.

- [ ] **Step 3: Document CLI and HTTP server orchestration**

Document every CLI command, parser construction, and dispatch path. Document `frontend_directory()`, request-handler dunder/HTTP methods, dataset status assembly, JSON decoding/encoding, log suppression, server startup, query extraction, and limit parsing. Add comments at input-validation, adapter/evaluator selection, response-status, and error boundaries. Reorder server imports without changing endpoints.

- [ ] **Step 4: Document the root runner and preserve delayed import semantics**

Use this structure in `run_evalhub.py`:

```python
"""允许在未安装包时从仓库根目录启动 EvalHub CLI。"""

import sys
from pathlib import Path

# 在导入项目包前把 ``src`` 布局目录加入搜索路径，支持直接运行仓库脚本。
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# 该导入有意位于路径初始化之后，否则直接运行脚本时无法找到 ``evalhub`` 包。
from evalhub.cli import main  # noqa: E402

if __name__ == "__main__":
    # 把 CLI 返回码交给解释器，保证 shell 能准确识别命令执行结果。
    raise SystemExit(main())
```

- [ ] **Step 5: Verify and commit the group**

```bash
.venv/bin/python -m pytest tests/test_cli_parser.py tests/test_ollama_status.py tests/test_server_frontend.py -q
.venv/bin/python -m ruff check src/evalhub/__init__.py src/evalhub/datasets src/evalhub/config.py src/evalhub/api src/evalhub/ollama.py src/evalhub/cli.py src/evalhub/server.py run_evalhub.py
git diff --check -- src/evalhub run_evalhub.py
git add src/evalhub/__init__.py src/evalhub/datasets src/evalhub/config.py src/evalhub/api src/evalhub/ollama.py src/evalhub/cli.py src/evalhub/server.py run_evalhub.py
git commit -m "docs: explain datasets and application entry points"
```

Expected: eight targeted tests pass, Ruff reports no errors for the group, and business outputs remain unchanged.

### Task 5: Annotate all tests

**Files:**
- Modify: all six files in the test group.

**Interfaces:**
- Consumes: the existing observable assertions and fakes.
- Produces: documented test intent, setup rationale, boundary conditions, and fake-response lifecycle without weakening assertions.

- [ ] **Step 1: Add module, class, test-method, helper, and dunder docstrings**

For every test, describe the behavior protected and why it matters. Document `_Response` as a deterministic `urlopen()` context-manager fake, including `__init__`, `__enter__`, `__exit__`, and `read()`. Test docstrings do not need empty `Args` or `Returns` sections.

- [ ] **Step 2: Add semantic Arrange/Act/Assert boundary comments**

Use Chinese comments that describe the specific scenario rather than generic phase labels. Example:

```python
# 构造一个已安装目标模型的 Ollama 响应，隔离真实本地服务和网络状态。
response = _Response(b'{"models":[{"name":"qwen2.5:0.5b"}]}')

# 同时替换命令探测和 HTTP 边界，使断言只验证状态归一化逻辑。
with (...):
    status = get_ollama_status()
```

Reorder imports in `test_ollama_adapter.py` and `test_server_frontend.py`. Do not change assertion values, exception expectations, or test discovery names.

- [ ] **Step 3: Verify and commit the tests**

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check tests
git diff --check -- tests
git add tests
git commit -m "docs: explain Python test scenarios"
```

Expected: all 13 tests pass, Ruff reports no errors for tests, and the commit contains exactly the six test files.

### Task 6: Run mechanical comment-compliance and repository validation

**Files:**
- Read: all 36 target Python files.
- Temporary create: `/tmp/evalhub_comment_compliance.py`
- Do not add the temporary checker to Git.

**Interfaces:**
- Consumes: Python ASTs, source tokens, Chinese docstrings, and Chinese comment boundaries.
- Produces: a deterministic zero/nonzero validation result with file and line diagnostics.

- [ ] **Step 1: Create the one-time compliance checker**

Create `/tmp/evalhub_comment_compliance.py` with `apply_patch` and this exact content:

```python
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TARGETS = sorted((ROOT / "src").rglob("*.py"))
TARGETS += sorted((ROOT / "tests").rglob("*.py"))
TARGETS.append(ROOT / "run_evalhub.py")
CHINESE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def chinese_count(text: str) -> int:
    return len(CHINESE.findall(text))


def detailed_chinese(text: str | None) -> bool:
    return text is not None and chinese_count(text) >= 8


def docstring_span(node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> range:
    if not node.body:
        return range(0)
    first = node.body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return range(0)
    if not isinstance(first.value.value, str):
        return range(0)
    return range(first.lineno, (first.end_lineno or first.lineno) + 1)


def check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    errors: list[str] = []
    relative = path.relative_to(ROOT)

    if not detailed_chinese(ast.get_docstring(tree, clean=False)):
        errors.append(f"{relative}:1 模块缺少详细中文 docstring")

    comments: dict[int, str] = {}
    logical_lines: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and detailed_chinese(token.string):
            comments[token.start[0]] = token.string
        elif token.type == tokenize.NEWLINE:
            logical_lines.add(token.start[0])

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not detailed_chinese(ast.get_docstring(node, clean=False)):
                errors.append(f"{relative}:{node.lineno} 类 {node.name} 缺少详细中文 docstring")
            continue
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not detailed_chinese(ast.get_docstring(node, clean=False)):
            errors.append(f"{relative}:{node.lineno} 方法 {node.name} 缺少详细中文 docstring")

        ignored = set(docstring_span(node))
        body_lines = [line for line in logical_lines if node.lineno < line <= (node.end_lineno or node.lineno)]
        streak = 0
        for line in sorted(set(body_lines) | set(comments)):
            if line in comments:
                streak = 0
            if line in body_lines and line not in ignored:
                streak += 1
                if streak > 5:
                    errors.append(f"{relative}:{line} 方法 {node.name} 连续有效代码超过 5 行")
                    break
    return errors


def main() -> int:
    errors = [error for path in TARGETS for error in check_file(path)]
    if errors:
        print("\n".join(errors))
        return 1
    print(f"通过：{len(TARGETS)} 个 Python 文件满足中文注释规则")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the checker and fix every reported source issue**

```bash
.venv/bin/python /tmp/evalhub_comment_compliance.py .
```

Expected: `通过：36 个 Python 文件满足中文注释规则`. Fix reported source files with semantic Chinese text, then rerun until exit status 0.

- [ ] **Step 3: Run full repository verification**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
git diff --check
git status --short
```

Expected: Ruff exits 0, all 13 tests pass, whitespace validation exits 0, and only intended task files remain changed or committed in the feature worktree.

- [ ] **Step 4: Commit compliance fixes if the checker required any**

```bash
git add src tests run_evalhub.py
git diff --cached --check
git commit -m "docs: complete Chinese comment coverage"
```

Expected: create this commit only when source fixes remain after Tasks 2–5; otherwise skip it without creating an empty commit.

### Task 7: Integrate, verify, and push main

**Files:**
- Merge: all feature-branch commits.
- Preserve: unrelated uncommitted files in the main checkout.

**Interfaces:**
- Consumes: a clean verified feature branch and the current remote `main` state.
- Produces: a verified local and remote `main` containing the complete comment coverage.

- [ ] **Step 1: Fetch remote state without modifying worktree files**

```bash
git fetch origin main
git log --oneline --left-right --cherry-pick main...origin/main
```

Expected: remote divergence is visible before integration. Do not force push.

- [ ] **Step 2: Safely integrate any remote advance into the feature branch**

If `origin/main` is not an ancestor of the feature branch, run:

```bash
git merge origin/main
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
.venv/bin/python /tmp/evalhub_comment_compliance.py .
```

Expected: merge completes without losing comments, Ruff exits 0, all tests pass, and every current Python file passes the checker.

- [ ] **Step 3: Merge the feature branch into local main**

From `/Users/nedonion/PycharmProjects/evalhub`, use a normal merge that preserves unrelated working-tree changes:

```bash
git merge --no-ff feature/full-python-chinese-comments -m "merge: document all Python code in Chinese"
```

Expected: merge succeeds because all overlapping Python working changes were preserved in Task 1; unrelated docs/frontend/shell changes remain uncommitted.

- [ ] **Step 4: Verify the exact merged tree**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
.venv/bin/python /tmp/evalhub_comment_compliance.py .
git diff --check HEAD^ HEAD
```

Expected: Ruff exits 0, all tests pass, the checker reports every current Python file compliant, and the merge diff has no whitespace errors.

- [ ] **Step 5: Push and verify remote main**

```bash
git push origin main
git fetch origin main
git rev-parse main
git rev-parse origin/main
```

Expected: push succeeds normally and both final hashes are identical.
