# EvalHub Repository Agent Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a repository-root `AGENTS.md` that enforces strict, executable engineering rules across EvalHub, with Python as the primary focus.

**Architecture:** Use one repository-specific instruction file at the root so every task receives the same baseline. The document records current project facts, module boundaries, development rules, validation commands, safety constraints, and a definition of done without changing application behavior or adding tools.

**Tech Stack:** Markdown, Python 3.11, pytest, Ruff, setuptools `src` layout, vanilla HTML/CSS/JavaScript.

## Global Constraints

- The minimum Python version is 3.11.
- Create one root `AGENTS.md`; do not create directory-level files with the same name.
- Write prose in Chinese while preserving commands, paths, and code identifiers verbatim.
- Do not add mypy, pre-commit, a frontend framework, CI tooling, or any other dependency.
- Do not modify Python, frontend, script, configuration, or build implementation files.
- Preserve all user changes already present in the working tree.
- Required validation commands are `.venv/bin/python -m ruff check .` and `.venv/bin/python -m pytest`.
- Every newly added or modified top-level function, instance method, class method, static method, and async method must have a detailed Chinese docstring.
- Blank lines, comments, and docstrings do not count as effective code lines; no run of effective code may exceed 5 lines without a detailed Chinese comment.
- The dense Chinese-comment rule applies only to newly added or modified Python code, not untouched legacy code.

---

## File Structure

- Create: `AGENTS.md` — repository-wide instructions for coding agents.
- Reference: `pyproject.toml` — authoritative Python version, pytest paths, Ruff line length, and lint rules.
- Reference: `docs/ARCHITECTURE.md` — authoritative module responsibilities and extension points.
- Reference: `docs/CODEX_WORKFLOW.md` — authoritative documentation synchronization workflow.
- Reference: `README.md` — authoritative local commands and current repository capabilities.

### Task 1: Add and verify repository-wide agent rules

**Files:**
- Create: `AGENTS.md`
- Test: no new automated test file; validate the instruction artifact and run the repository's existing quality suite.

**Interfaces:**
- Consumes: repository layout and tool configuration from `pyproject.toml`, `README.md`, `docs/ARCHITECTURE.md`, and `docs/CODEX_WORKFLOW.md`.
- Produces: root-level natural-language instructions consumed by coding agents for all files in the repository.

- [ ] **Step 1: Confirm the target does not already exist**

Run:

```bash
test ! -e AGENTS.md
```

Expected: exit status 0. If the file exists, inspect it and merge deliberately instead of overwriting it.

- [ ] **Step 2: Create the exact repository instruction file**

Create `AGENTS.md` with this content:

````markdown
# EvalHub Agent 工程规范

## 适用范围与优先级

- 本文件适用于整个仓库。
- 优先遵循系统、开发者和用户的直接指令；如将来某个子目录存在更具体的 `AGENTS.md`，则其规则只覆盖该子目录。
- 目标是交付范围最小、行为正确、易于维护且经过验证的变更。
- 只修改当前任务需要的文件；不要顺手重构、格式化或整理无关代码。

## 项目事实

- Python 最低版本为 3.11，包采用 `src` 布局，主包位于 `src/evalhub/`。
- 构建和工具配置以 `pyproject.toml` 为准。
- 单元测试位于 `tests/`，使用 pytest；静态检查使用 Ruff，行宽为 100。
- 核心模块职责以 `docs/ARCHITECTURE.md` 为准，开发文档沉淀位置以 `docs/CODEX_WORKFLOW.md` 为准。
- `frontend/` 当前使用原生 HTML、CSS 和 JavaScript，不使用前端构建工具。

## 常用命令

```bash
# 安装本项目及开发依赖（仅在环境尚未就绪时）
.venv/bin/python -m pip install -e ".[dev]"

# 运行全部测试
.venv/bin/python -m pytest

# 运行单个测试文件或测试用例
.venv/bin/python -m pytest tests/test_runner.py -q
.venv/bin/python -m pytest tests/test_runner.py::test_runner_generates_sample_results_and_report -q

# 执行静态检查
.venv/bin/python -m ruff check .

# 本地启动
./scripts/start_local.sh
```

- 优先使用仓库内的 `.venv/bin/python`，避免误用系统 Python。
- 先运行与改动最相关的测试，再运行完整检查。
- 单元验证不得依赖真实网络、下载公开数据集或启动 Ollama；只有任务明确要求集成验证时才可使用这些外部资源。

## 修改前要求

- 先阅读 `pyproject.toml`、相关实现、现有测试和对应文档，沿用现有模式。
- 先运行 `git status --short`，识别并保护用户已有的未提交修改。
- 在动手前明确预期行为、边界条件和验证方式。
- 修复缺陷时，先添加能够稳定复现问题的回归测试，再修改实现。
- 行为不明确时，优先从测试、公开接口和文档中获取证据；会改变产品语义时再向用户确认。

## Python 代码规范

### 类型与接口

- 新增或修改的公共函数、方法、类属性和返回值必须提供准确类型标注。
- 优先使用 Python 3.11 原生写法，如 `list[str]`、`dict[str, object]` 和 `X | None`。
- 不使用无意义的 `Any`。外部动态数据确实需要 `Any` 时，应在边界处尽快校验并收窄类型。
- 保持公共 CLI、API、配置格式和可导入符号向后兼容；如任务要求破坏性变更，必须同步测试、文档和迁移说明。

### 结构与可读性

- 遵循 Ruff 在 `pyproject.toml` 中配置的 `E`、`F`、`I`、`UP`、`B` 规则和 100 字符行宽。
- 使用清晰的领域命名；避免无信息量的缩写、布尔参数堆叠和散落的魔法值。
- 一个函数只承担一个清晰职责。复杂分支应拆成可独立测试的小函数，但不要为单次调用制造无价值抽象。
- 优先复用现有实体、协议、基类和注册机制，不复制相同业务逻辑。
- 模块导入时不得执行网络请求、创建文件、启动服务或读取重型数据。

### 中文注释强制规则

- 本规则只约束本次新增或修改的 Python 代码，不要求为了当前任务补写未触及的旧代码。
- 每个新增或修改的顶层函数、实例方法、类方法、静态方法和异步方法都必须提供详细中文 docstring。
- docstring 必须说明方法用途，并按实际情况说明参数、返回值和可能抛出的异常；不得只复述方法名。
- 函数或方法体按有效代码行计数；空行、已有注释和 docstring 不计入 5 行。
- 任意连续有效代码不得超过 5 行而没有详细中文注释；达到 5 行时，应在下一个逻辑代码块前加入注释。
- 中文注释必须解释目的、业务含义、约束或原因，不得用“执行下面代码”“处理数据”等无信息文字充数。
- 注释放在完整表达式和逻辑块边界，不得拆开装饰器、函数签名、多行表达式、多行字符串或链式调用。
- 注释密度与代码结构冲突时，应先把逻辑拆成更小的方法，再按同一规则添加详细中文注释。

### 异常、时间与 I/O

- 捕获最具体的异常；禁止裸 `except`，也不要无说明地使用 `except Exception`。
- 不静默吞掉异常。转换异常时使用 `raise ... from exc` 保留因果链，并避免泄露密钥或敏感响应。
- 库代码不得用 `print()` 充当日志；`print()` 仅用于明确的 CLI 用户输出。
- 时间值使用带时区的 UTC 时间，沿用 `datetime.now(UTC)` 的现有模式。
- 文件读取显式使用 UTF-8；路径操作优先使用 `pathlib.Path`，并让文件系统错误保留可诊断上下文。
- 环境变量和配置解析集中在配置或入口边界，不散落到领域逻辑中。

## 架构边界

- `evalhub.domain`：实体、枚举和仓储协议。不得依赖 API、具体适配器、具体 Registry 或框架。
- `evalhub.engine`：编排评测运行和报告生成。依赖领域对象以及 adapter/evaluator 抽象，不依赖其具体实现。
- `evalhub.adapters`：封装模型推理边界。新增模型接入时实现 `ModelAdapter.generate()`，不要在 Runner 中按模型类型分支。
- `evalhub.evaluators`：封装评测策略。新增指标时实现 `Evaluator.evaluate()` 并注册到 `EvaluatorRegistry`。
- `evalhub.registry`：实现领域仓储协议。新增持久化实现时不要让领域层依赖数据库或 ORM。
- `evalhub.datasets`：负责数据集目录、下载、缓存和样本转换；外部格式不得泄漏到领域核心。
- `evalhub.api`、`evalhub.cli`、`evalhub.server` 和 `run_evalhub.py`：作为入口与装配层，负责输入转换、依赖选择和输出，不承载可复用业务逻辑。
- 避免循环依赖。跨模块调用优先依赖稳定的公共接口，不从其他模块的私有实现中导入符号。

## 测试规范

- 新功能必须测试正常路径和关键边界；缺陷修复必须包含在修复前失败、修复后通过的回归测试。
- 测试名称应描述可观察行为，遵循 `test_<behavior>`，不要只复述被测函数名。
- 测试必须确定、隔离且快速，不依赖执行顺序、墙上时钟、真实网络、Ollama 服务或残留本地状态。
- 在外部边界使用轻量 fake、stub 或 mock；优先断言公开结果和状态，不绑定内部调用细节。
- 不以删除断言、降低覆盖范围、放宽业务条件或跳过失败测试来让测试通过。
- 修改 CLI 参数、API 响应、数据加载、adapter、evaluator 或 Runner 时，更新对应测试。
- 测试使用临时目录和 pytest fixture；不得向仓库的 `data/`、`reports/`、`artifacts/` 或 `.runtime/` 写入持久测试产物。

## 前端、脚本与配置

- 前端改动沿用现有原生 HTML、CSS 和 JavaScript；未经任务明确要求，不引入 React、Node、打包器或新依赖。
- 前端调用的 API 字段必须与 Python 服务端保持一致；变更响应结构时同步更新调用方、测试和 `docs/API.md`。
- 修改 shell 脚本时启用严格失败语义，正确引用路径和变量，并兼顾当前 macOS/zsh 本地运行方式。
- 新配置必须提供安全默认值和清晰错误信息；敏感值只能来自环境变量，示例写入 `.env.example` 时只使用虚拟值。
- 不硬编码个人绝对路径。文档可保留现有项目路径示例，但实现和脚本应从仓库位置推导路径。

## 依赖、文档与兼容性

- 不因便利添加第三方依赖。任务确实需要新依赖时，说明理由，将其放入正确的 `pyproject.toml` 依赖组，并同步安装文档。
- 运行方式、依赖或本地服务变化时更新 `README.md`、`docs/LOCAL_RUN.md` 或 `docs/OLLAMA.md`。
- API 变化更新 `docs/API.md`；架构或模块职责变化更新 `docs/ARCHITECTURE.md`；数据结构变化更新 `docs/DATA_MODEL.md`；产品范围和阶段计划变化更新 `docs/PRD.md` 或 `docs/ROADMAP.md`。
- 文档只记录已实现且已验证的行为，示例命令应能从仓库根目录执行。

## 安全与仓库卫生

- 不读取、输出或提交真实密钥、令牌、`.env` 内容和敏感模型响应。
- 不提交数据集、模型产物、日志、报告、缓存、虚拟环境或 IDE 文件；遵循 `.gitignore`。
- 不覆盖、回退、暂存或提交用户的无关修改。只对任务范围内文件执行精确操作。
- 禁止使用破坏性 Git 或文件命令清理工作区。遇到冲突或不明修改时停止并说明。
- 不伪造测试结果、外部服务状态、性能数据或运行截图。

## 完成定义

交付前必须确认：

1. 改动范围与请求一致，没有无关重构或生成文件。
2. 新增或变更行为有相应测试，相关文档已同步。
3. `.venv/bin/python -m ruff check .` 通过。
4. `.venv/bin/python -m pytest` 通过。
5. `git diff --check` 没有空白错误，diff 中没有密钥、调试输出或意外的大文件。

若某项检查因环境或外部依赖无法执行，必须明确列出未执行的命令、原因和剩余风险，不得宣称全部通过。

最终回复应简要说明：

- 修改了什么以及为什么。
- 实际运行了哪些验证命令及其结果。
- 是否存在未验证项、兼容性影响或后续工作。
````

- [ ] **Step 3: Validate structure and repository-specific references**

Run:

```bash
test -f AGENTS.md
rg -n '^## (适用范围与优先级|项目事实|常用命令|修改前要求|Python 代码规范|架构边界|测试规范|前端、脚本与配置|依赖、文档与兼容性|安全与仓库卫生|完成定义)$' AGENTS.md
rg -n '\.venv/bin/python -m (ruff check \.|pytest)' AGENTS.md
rg -n 'src/evalhub/|docs/ARCHITECTURE\.md|docs/CODEX_WORKFLOW\.md|frontend/' AGENTS.md
rg -n '详细中文 docstring|任意连续有效代码不得超过 5 行|空行、已有注释和 docstring 不计入 5 行|不要求为了当前任务补写未触及的旧代码' AGENTS.md
```

Expected: the file check exits 0; the heading command returns all 11 sections; both required validation commands, all repository-specific references, and all four dense Chinese-comment requirements are present.

- [ ] **Step 4: Run content and project quality checks**

Run:

```bash
git add AGENTS.md
git diff --cached --check
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
```

Expected: all commands exit 0. The existing suite should pass without application code changes.

- [ ] **Step 5: Review and commit only the intended artifact**

Run:

```bash
git status --short
git diff --cached -- AGENTS.md
git diff --cached --stat
git commit -m "docs: add repository agent engineering rules"
```

Expected: the staged diff contains only `AGENTS.md`; whitespace validation passes; the commit succeeds without staging any pre-existing user changes.
