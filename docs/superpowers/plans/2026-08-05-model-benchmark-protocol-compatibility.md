# 模型与 Benchmark 协议兼容修复实施计划

> **执行要求：** 按 TDD 顺序逐项完成；每项先让回归测试失败，再写最小实现并运行相关测试。

**目标：** 修复思考模型空响应、Benchmark 输出协议错配和错误评分问题，使控制台当前 13 个模型通过同一套、可复现的 7 项 Hexagon 评分标准。

**方案：** 在现有 Ollama 适配器、Benchmark Registry 和 Evaluator Registry 三个共享边界分别增加最小的声明式配置。模型配置只决定传输参数，Benchmark 配置只决定生成预算与统一评分器；所有组合在工作流创建时冻结并进入协议指纹，不为具体模型放宽评分标准。

**技术栈：** Python 3.11、pytest、urllib、原生 React/TypeScript、Vitest。

---

## 任务 1：固定模型生成协议并识别不完整响应

**文件：**

- 新建：`src/evalhub/model_protocols.py`
- 修改：`src/evalhub/adapters/base.py`
- 修改：`src/evalhub/adapters/ollama.py`
- 修改：`src/evalhub/adapters/__init__.py`
- 修改：`src/evalhub/ollama.py`
- 测试：`tests/test_model_protocols.py`
- 测试：`tests/test_ollama_adapter.py`
- 测试：`tests/test_ollama_status.py`

### 1.1 先写失败测试

- 断言 13 个控制台模型都有协议；思考模型为 `think=False`，非思考模型不发送该字段。
- 断言未知已安装模型状态为 `unsupported`，未安装已注册模型为 `static_only`，已安装已注册模型为 `verified`。
- 断言 Ollama 把 `think` 放在请求顶层，其他生成参数仍放在 `options`。
- 断言 `response`、`done`、`done_reason` 类型不正确时协议失败。
- 断言空响应且 `done_reason=length` 抛出稳定错误 `generation_incomplete`；其他空响应抛出 `empty_model_response`。
- 断言非空但 `done_reason=length` 返回文本并保留终止诊断。

运行：

```bash
.venv/bin/python -m pytest tests/test_model_protocols.py tests/test_ollama_adapter.py tests/test_ollama_status.py -q
```

预期：新断言失败，证明当前代码没有思考开关、协议状态和完成原因处理。

### 1.2 写最小实现

- 用一个不可变映射登记 13 个精确模型标签及 `think` 行为，不新增工厂或模型家族推断。
- 在 `adapters.base` 增加向后兼容的 `ModelGeneration` 值对象和 `ModelGenerationError(code)`；第三方适配器继续允许返回 `str`。
- `OllamaAdapter.generate()` 返回 `ModelGeneration`，严格校验 JSON 字段，并在无可评分文本时 fail closed。
- `get_ollama_status()` 只增加可选展示字段，不改变旧字段含义。

### 1.3 验证

运行同一组测试，预期全部通过。

## 任务 2：为七项 Hexagon 固定回答协议和预算

**文件：**

- 修改：`src/evalhub/benchmarks/models.py`
- 修改：`src/evalhub/benchmarks/registry.py`
- 修改：`src/evalhub/datasets/catalog.py`
- 新建：`src/evalhub/evaluators/bbh.py`
- 修改：`src/evalhub/evaluators/__init__.py`
- 修改：`src/evalhub/evaluators/registry.py`
- 测试：`tests/test_benchmark_registry.py`
- 新建：`tests/test_bbh_evaluator.py`
- 新建：`tests/test_numeric_evaluator.py`
- 修改：`tests/test_hexagon_sources.py`

### 2.1 先写失败测试

- 断言 Hexagon 版本为 `1.2.0`，七项预算依次为 256、1024、512、512、1024、256、256。
- 断言每项均有不可变 `answer_protocol_version`。
- 断言 GSM8K 使用 `numeric_exact_match`，可接受带推理文本但数值正确的答案。
- 断言 BBH 五种选定子任务分别识别布尔、是非、选项字母、valid/invalid；只取高置信度末行/显式答案，歧义或无答案得 0。

运行：

```bash
.venv/bin/python -m pytest tests/test_benchmark_registry.py tests/test_bbh_evaluator.py tests/test_numeric_evaluator.py tests/test_hexagon_sources.py -q
```

### 2.2 写最小实现

- 在 `BenchmarkSpec` 增加带默认值的 `answer_protocol_version`，保持非 Hexagon 构造兼容。
- 在现有 Registry 中用两个小映射覆盖 Hexagon 的生成预算和回答协议版本，避免七套执行分支。
- 修正数据集目录评分器 ID，并注册一个只处理当前五类 BBH 答案域的 `BBHAnswerEvaluator`。

### 2.3 验证

运行同一组测试，预期全部通过。

## 任务 3：让 Runner、HumanEval 和任务错误边界消费统一生成结果

**文件：**

- 修改：`src/evalhub/engine/runner.py`
- 修改：`src/evalhub/benchmarks/humaneval.py`
- 修改：`src/evalhub/tasks/executor.py`
- 测试：`tests/test_runner.py`
- 修改：`tests/test_humaneval_sandbox.py`
- 修改：`tests/test_task_executor.py`

### 3.1 先写失败测试

- 断言 Runner 同时兼容旧适配器的 `str` 和 Ollama 的 `ModelGeneration`，并把 `done_reason` 写入样本 metadata。
- 断言 HumanEval 接受纯 completion、单个 Python fenced block，以及包含目标 `entry_point` 的完整函数；隐藏测试始终只进入沙箱。
- 断言 `ModelGenerationError` 跨子进程保留稳定 `error_type`，不会产生 0 分样本。

运行：

```bash
.venv/bin/python -m pytest tests/test_runner.py tests/test_humaneval_sandbox.py tests/test_task_executor.py -q
```

### 3.2 写最小实现

- 在两个模型调用点复用一个生成结果解包函数，避免调用方分别判断类型。
- HumanEval 仅剥离唯一 Python 代码围栏；若模型返回完整目标函数，则把它作为完整源码提交，否则沿用官方 prompt + completion。
- 在任务子进程边界单独捕获 `ModelGenerationError` 并透传其错误代码。

### 3.3 验证

运行同一组测试，预期全部通过。

## 任务 4：冻结两轴协议并暴露兼容状态

**文件：**

- 修改：`src/evalhub/tasks/workflow.py`
- 修改：`src/evalhub/tasks/runtime.py`
- 修改：`src/evalhub/tasks/models.py`
- 修改：`tests/test_workflow_runtime.py`
- 修改：`tests/test_task_api.py`
- 修改：`frontend/src/types.ts`
- 修改：`frontend/src/components/dashboard/ModelSelector.tsx`
- 修改：`frontend/src/components/dashboard/ModelSelector.test.tsx`
- 修改：`frontend/src/components/dashboard/EvaluationResultDetail.tsx`
- 修改：`frontend/src/components/dashboard/EvaluationResultDetail.test.tsx`

### 4.1 先写失败测试

- 断言 Ollama Hexagon 任务对未知模型以 `model_protocol_not_registered` 拒绝；Oracle 和非模型工作流不受影响。
- 断言工作流节点冻结有效生成配置、模型协议版本和回答协议版本，且都参与 `protocol_fingerprint`。
- 断言恢复运行只使用节点冻结值，不重新读取当前 Registry。
- 断言模型选择器显示 verified/static-only/unsupported，结果账本显示模型协议、回答协议和各 Benchmark 预算。

运行：

```bash
.venv/bin/python -m pytest tests/test_workflow_runtime.py tests/test_task_api.py -q
cd frontend && npm test -- --run src/components/dashboard/ModelSelector.test.tsx src/components/dashboard/EvaluationResultDetail.test.tsx
```

### 4.2 写最小实现

- 只在 `adapter=ollama` 的 Hexagon 模型评测创建阶段解析模型协议并合并 `think`；不改变 Oracle 与核心套件现有路径。
- 节点和可复现性账本增加版本化字段；保留原 `generation_config` 字段以兼容既有前端和历史任务。
- 前端新增非阻塞状态展示，不改变下载和选择交互。

### 4.3 验证

运行同一组测试，预期全部通过。

## 任务 5：同步文档并完成静态、单元和真实兼容验证

**文件：**

- 修改：`README.md`
- 修改：`docs/architecture/20260804_系统架构.md`
- 修改：`docs/architecture/20260804_API接口草案.md`
- 修改：`docs/getting-started/20260804_本地Benchmark评测故障排查.md`

### 5.1 文档

- 说明 13 个模型协议状态、七项统一答案协议、空响应阻塞语义和版本迁移。
- 明确 `static_only` 代表只完成静态合同校验，未下载模型不能宣称真实通过。

### 5.2 全量验证

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
cd frontend && npm test -- --run
cd frontend && npm run build
git diff --check
```

### 5.3 本地 Ollama 兼容验证

- 对 9 个已安装模型分别执行七项协议各一个真实样本，确认非空输出、可解析性和沙箱边界。
- 再对 9 个已安装模型运行完整 30 题 Hexagon；只记录真实结果，不把知识性答错当成协议失败。
- 对 4 个未安装推荐模型只运行 13×7 静态合同测试，不下载模型，并在交付中列为待真实验证。

若本地 Ollama 或 Docker 不可用，记录未执行命令和剩余风险，不宣称对应组合已通过。
