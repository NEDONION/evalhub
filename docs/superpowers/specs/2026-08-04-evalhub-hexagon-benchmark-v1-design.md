# EvalHub Hexagon Benchmark v1 设计

## 目标

为 EvalHub 增加一个从专业公开 Benchmark 官方来源下载的六维模型评测套件。套件固定
包含知识、指令遵循、数学、综合推理、代码、安全可信六个维度，每维 10 个英文样本，
共 60 个样本。完整运行一次即可生成可重复比较的六边形能力画像。

每个样本保留官方英文原文和 EvalHub 中文辅助翻译。只有英文原文进入模型和评分协议；
中文翻译只供人在结果详情中阅读，不增加模型调用次数，也不参与任何分数计算。

## 产品约束

- 套件稳定 ID 为 `evalhub-hexagon-v1`，协议版本为 `1.0.0`。
- 每个维度恰好评测 10 个固定样本，完整运行共调用模型 60 次。
- 样本必须来自已确认的专业公开 Benchmark，不创建 EvalHub 自编替代题。
- 首次准备从官方来源下载；后续运行复用校验通过的本地缓存。
- 抽样清单固定并版本化，不在每轮运行时重新随机抽样。
- 模型只接收官方英文题目，中文翻译只作为显示元数据。
- 评分遵循各来源 Benchmark 的任务协议，不使用通用 LLM-as-a-Judge。
- 模型对比必须使用相同套件版本、完整 60 个样本和相同生成配置。

## 非目标

- 60 个样本构成 EvalHub 的固定 Mini Suite，不宣称等同于任何来源 Benchmark 的全量分数。
- 不把中文辅助翻译宣传为数据集官方译文，也不单独运行中文评测。
- 不在每次运行时换题，不根据已测模型表现挑题或调答案。
- 不把缺少数据、执行器或沙箱的维度伪造为零分。
- 不直接在宿主机运行模型生成的代码。

## 方案选择

采用“官方文件下载 + 固定抽样清单 + 本地规范化缓存”。仓库保存来源元数据、选择器、
预期摘要和中文辅助翻译，不保存一套脱离来源追踪的自编英文题库。准备阶段从固定官方
revision 下载并校验；运行阶段只读取已经规范化的 60 个样本。

没有采用每轮随机抽样，因为不同模型会收到不同题目，无法精准比较。没有采用直接提交
60 道英文题到仓库，因为这会绕过用户要求的官方下载链路，也不利于核验来源 revision。

## 数据源与能力映射

Suite 由七个来源切片组成；安全可信维度由两个各 5 题的专业 Benchmark 共同覆盖：

| Benchmark ID | 能力 | 官方来源 | 样本数 | 核心指标 |
| --- | --- | --- | ---: | --- |
| `hexagon-mmlu` | 知识 | MMLU test | 10 | Accuracy |
| `hexagon-ifeval` | 指令遵循 | IFEval | 10 | Prompt-level strict accuracy |
| `hexagon-gsm8k` | 数学 | GSM8K test | 10 | Numeric exact match |
| `hexagon-bbh` | 综合推理 | BIG-Bench Hard | 10 | Exact match / Accuracy |
| `hexagon-humaneval` | 代码 | HumanEval | 10 | Pass@1 |
| `hexagon-truthfulqa` | 安全可信 | TruthfulQA | 5 | Binary multiple-choice accuracy |
| `hexagon-bbq` | 安全可信 | BBQ | 5 | Three-choice accuracy |

官方来源固定为：

- MMLU：`https://github.com/hendrycks/test`
- IFEval：`https://github.com/google-research/google-research/tree/master/instruction_following_eval`
- GSM8K：`https://github.com/openai/grade-school-math`
- BBH：`https://github.com/suzgunmirac/BIG-Bench-Hard`
- HumanEval：`https://github.com/openai/human-eval`
- TruthfulQA：`https://github.com/sylinrl/TruthfulQA`
- BBQ：`https://github.com/nyu-mll/BBQ`

每个来源记录官方主页、许可、下载 URL、固定 commit 或文件 revision、原文件 SHA-256、
split 和实际使用的来源样本标识。缓存目录继续位于被 `.gitignore` 排除的 `data/` 下。

## 固定抽样清单

仓库内置一个版本化选择清单，而不是内置英文题目正文。每个逻辑样本至少记录：

```json
{
  "id": "hexagon_gsm8k_01",
  "capability": "mathematics",
  "source": "gsm8k",
  "source_revision": "official-git-commit-sha",
  "split": "test",
  "source_key": "test.jsonl:42",
  "input_zh": "仅供页面显示的中文辅助翻译",
  "translation_version": "evalhub-zh-v1"
}
```

有官方稳定 ID 时直接使用；没有稳定 ID 时使用固定文件相对路径和一基行号。准备阶段必须
确认选择器恰好解析到一个来源样本，并为规范化后的英文输入、答案和中文翻译分别计算摘要。

选择清单一次生成后随 Suite v1 冻结。初始选择使用稳定分层规则，避免人工按模型表现挑题：

- MMLU 从 10 个不同学科各选 1 题。
- IFEval 覆盖 10 个不同的可验证指令类型。
- GSM8K 从 test split 按固定种子哈希选择 10 题。
- BBH 从 10 个不同任务各选 1 题。
- HumanEval 按固定任务 ID 选择 10 题。
- TruthfulQA 从 5 个不同类别各选 1 题。
- BBQ 从 5 个不同偏见类别各选 1 题，并保留官方上下文类型。

固定种子为 `evalhub-hexagon-v1`。分层内按 `SHA-256(seed + source_key)` 升序选择，最终
选中的完整 `source_key` 列表写入清单，后续运行不再执行抽样算法。

## 英文评分与中文展示

规范化样本同时保留：

- `input`：送入模型的官方英文题目及按官方协议构造的提示。
- `reference`：官方答案、规则或测试引用。
- `input_zh`：英文题目和选项的中文辅助翻译。
- `reference_zh`：可安全展示时提供的中文答案说明。
- `source_metadata`：来源、revision、split、source key、许可和原文摘要。

模型适配器、Evaluator 和 HumanEval 沙箱只能读取英文 `input` 与官方 `reference`。
`input_zh` 和 `reference_zh` 只进入样本结果详情。页面明确标注“EvalHub 中文辅助翻译，
非官方译文”；翻译修订只提升可读性，不改变英文分数或来源 Benchmark 名称。

## 来源协议

### MMLU

使用官方 test CSV，保留原始问题、四个选项和答案字母。10 个样本来自不同学科，使用
现有 `choice_letter` 评分器计算 Accuracy。

### IFEval

使用官方英文 prompt 与 instruction ID。所选 10 题只覆盖能够本地确定性验证的官方
指令类型，按官方规则实现 prompt-level strict accuracy；不能退化为字符串参考答案。

### GSM8K

使用官方 test JSONL，保留问题和 `####` 后的最终答案，复用现有数值答案提取与
`numeric_exact_match`。

### BBH

从 10 个 BBH 任务各取一个样本，保留任务提示结构，并按对应任务的官方目标执行精确
匹配或选择题评分。清单同时记录 task name 和任务内样本位置。

### HumanEval

使用官方问题定义、函数签名和隐藏测试。每题生成一次候选实现，以 Pass@1 计分。
模型生成代码只能在固定 Docker 镜像中执行，限制时间、内存、进程、网络和挂载；Docker
或镜像未就绪时该维度阻塞，禁止回退到宿主机执行。

### TruthfulQA 与 BBQ

TruthfulQA 使用官方推荐的二选一形式并固定选项排列；BBQ 保留官方三选一格式、上下文
类型和类别。两者各占安全可信维度 50% 权重，合计正好 10 题。

## 执行与数据流

```text
选择 evalhub-hexagon-v1
  -> 读取固定选择清单
  -> 下载或复用七个官方来源缓存
  -> 校验 revision、原文件摘要和 60 个 source key
  -> 生成只含所选样本的规范化本地缓存
  -> 六个文本执行器使用英文题目，代码执行器使用 Docker
  -> 按来源协议形成七个 Benchmark 结果
  -> 聚合为六个百分制能力分和 60 题总分
  -> 结果详情并排展示英文原题与中文辅助翻译
```

完整模型对比必须使用 `sample_mode=all`。quick 模式仅检查链路，不进入正式比较。
文本任务使用 Registry 固定的 `temperature=0`；HumanEval 同样只生成一个确定性候选。

## 评分与比较

- 每个来源先保存其官方协议下的原始指标。
- 六个能力分均转换到 `0-100`，但不做随机基线校正或难度加权。
- 知识、指令遵循、数学、综合推理、代码均由各自 10 题的平均分得到。
- 安全可信为 TruthfulQA 5 题与 BBQ 5 题合并后的 10 题正确率；两个切片权重相同。
- 六维全部完成时，总分为六维算术平均；因为每维都是 10 题，也等于 60 题总体平均。
- 单维最小可见分差为 10 分，同分不额外制造小数排名。
- 只有 Suite 版本、来源 revision、选择清单摘要、提示版本和生成参数全部相同的完整结果
  才允许横向比较。

## 错误处理

- 下载失败保留已验证旧缓存；没有可用缓存时准备节点失败，不启动模型调用。
- revision、文件 SHA-256 或 source key 不匹配时阻塞，禁止静默改用上游最新内容。
- 许可或来源元数据缺失时对应来源不可进入 ready 状态。
- 中文翻译缺失不改变英文分数，但测试和页面必须明确显示翻译缺失，不能伪装成已翻译。
- 模型输出格式错误记为该题零分，并保留原始输出和评分原因。
- 模型服务、超时和沙箱错误沿用现有重试语义；未执行题目不记零分。
- HumanEval Docker 不可用时代码维度未评测，整轮不能进入完整模型比较。
- 某来源失败时保留其他来源结果和覆盖率，报告状态为 partial 或 unassessed。

## 测试与验收

自动验证覆盖：

1. 选择清单恰好包含 60 个唯一逻辑 ID，每维恰好 10 个来源样本。
2. 七个来源切片数量严格为 10、10、10、10、10、5、5。
3. 每条记录都包含来源、revision、split、source key、摘要和非空中文翻译。
4. 下载器使用固定 URL、原子替换和 SHA-256 校验；单元测试只使用临时目录和本地响应。
5. 每个选择器在固定 fixture 中恰好命中一个样本，顺序与清单一致。
6. IFEval 所选规则通过官方兼容的正例和反例；不能用 exact-match 冒充规则验证。
7. GSM8K、MMLU、BBH、TruthfulQA 和 BBQ 的答案转换有来源格式回归测试。
8. HumanEval 单元测试使用 fake 沙箱；可用环境下单独运行 Docker 集成验证。
9. Oracle/固定适配器完整执行产生 60 个样本、六维满分和成功 Suite 状态。
10. 错误回答只降低对应维度，缺失 Docker 或损坏缓存产生未评测而不是虚假零分。
11. API 列出 `evalhub-hexagon-v1` 的六维覆盖、60 个配置样本和真实准备状态。
12. 结果详情同时显示英文原题、中文辅助翻译、来源和 sample key，评分只引用英文输入。
13. 原有行业核心 Suite、GSM8K、MMLU 和 Agent Coding Mini 测试继续通过。

交付前运行相关测试、完整 pytest、Ruff、`git diff --check`，并在 Docker 可用时运行
HumanEval 沙箱集成测试。任何未运行的外部集成验证必须在交付说明中列出。

## 完成标准

- 页面可准备并选择 `evalhub-hexagon-v1`，清楚显示七个专业来源及各自授权信息。
- 首次准备从固定官方 URL 下载并验证来源，之后可以复用缓存离线运行。
- 完整运行向模型发送恰好 60 个英文样本，六个维度各有 10 个有效评分。
- 结果详情为每个样本显示英文原题和中文辅助翻译，翻译不出现在模型请求中。
- HumanEval 仅在 Docker 沙箱内执行，未就绪时准确报告阻塞。
- 完整结果可由来源 revision、原文件摘要、选择清单摘要和样本 source key 追溯。
- 现有公开 Benchmark 套件、API 和历史结果保持兼容。
