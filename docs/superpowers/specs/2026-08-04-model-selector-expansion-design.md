# Model Selector Expansion Design

## Goal

扩充 EvalHub 的本地 Ollama 模型目录，让模型评测与 Agent 评测各自展示适用候选；把当前原生
`select` 替换为独立设计的富下拉，同时复用现有模型下载确认、进度和可用性校验流程。

## Scope

- 服务端继续以 `evalhub.ollama.RECOMMENDED_OLLAMA_MODELS` 作为唯一推荐目录。
- 每个推荐模型声明适用评测类型：`model`、`agent` 或两者。
- 本机已安装但不在推荐目录中的自定义模型继续同时出现在两类评测中，避免破坏现有使用方式。
- 前端只消费 `/api/ollama/status` 返回的 `model_options`，不维护第二份模型名单。
- 不修改 Pi Runner、评分器、Benchmark 或模型下载 API。
- 不新增前端依赖。

## Catalog

### Agent candidates

Agent 列表优先提供 Ollama 官方声明支持工具调用、Agent 工作流或 Agent 编码的模型：

| Model | Display label | Estimated size | Purpose |
| --- | --- | ---: | --- |
| `granite4.1:3b` | Granite 4.1 3B | 2.1 GB | 已验证的轻量工具调用基线 |
| `qwen3:4b` | Qwen 3 4B | 2.5 GB | 小体积工具模型 |
| `qwen3:8b` | Qwen 3 8B | 5.2 GB | 平衡工具调用、中文与代码理解 |
| `qwen3:14b` | Qwen 3 14B | 9.3 GB | 更高能力通用 Agent 档 |
| `ministral-3:8b` | Ministral 3 8B | 6.0 GB | 原生函数调用与 Agent 工作流 |
| `gemma4:12b` | Gemma 4 12B | 7.6 GB | 推理、编码与 Agent 工作流 |
| `lfm2.5:8b` | LFM 2.5 8B-A1B | 5.2 GB | 快速连续工具调用 |
| `north-mini-code-1.0:q4_K_M` | North Mini Code 1.0 | 19 GB | Agent 编码与终端任务高能力档 |

`qwen2.5-coder:7b` 不进入 Agent 推荐列表：本机实测中 Ollama 把工具 JSON 放入普通文本而非
结构化 `tool_calls`。它即使已经安装也只出现在模型评测列表，不出现在 Agent 列表。

### Model-evaluation candidates

答题评测包含全部 Agent 候选，并补充适合生成式、推理和代码答题的轻量模型：

| Model | Display label | Estimated size | Purpose |
| --- | --- | ---: | --- |
| `qwen2.5:0.5b` | Qwen 2.5 0.5B | 397 MB | 极小型答题基线 |
| `qwen2.5:1.5b` | Qwen 2.5 1.5B | 986 MB | 轻量通用答题 |
| `deepseek-r1:1.5b` | DeepSeek R1 1.5B | 1.1 GB | 轻量推理对照 |
| `qwen2.5-coder:7b` | Qwen 2.5 Coder 7B | 4.7 GB | 代码生成与修复答题 |

服务端合并结果仍保持“本机已安装项优先、未安装推荐项随后”的稳定排序，并按模型名去重。

## API Contract

`ModelOption` 增加两个字段：

```json
{
  "evaluation_types": ["model", "agent"],
  "capability_label": "Agent 编码"
}
```

- `evaluation_types` 是非空数组，只允许 `model` 和 `agent`。
- `capability_label` 是用于短标签展示的稳定中文文本。
- 推荐模型使用目录中的字段。
- 未命中目录的已安装模型默认支持两种评测，标签为“本机模型”。
- 现有 `name`、`label`、`description`、`installed`、`size_bytes`、`size_kind` 保持兼容。

## Selector Interaction

新增一个仅服务于模型选择的 `ModelSelector` 组件，不泛化为全站下拉框。

```text
模型
┌─────────────────────────────────────────────────────────┐
│ Ministral 3 8B   Agent 工具   6.0 GB   推荐下载       ▾ │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ 已安装                                                   │
│ ✓ Granite 4.1 3B        Agent 基线   2.1 GB             │
│                                                         │
│ 推荐下载                                                 │
│   Ministral 3 8B        Agent 工具   约 6.0 GB           │
│   Gemma 4 12B           Agent 编码   约 7.6 GB           │
└─────────────────────────────────────────────────────────┘
```

- 触发器展示当前模型的友好名称、能力标签、容量和安装状态。
- 面板按“已安装”“推荐下载”分组；组内沿用服务端顺序。
- Agent 模式只显示 `evaluation_types` 包含 `agent` 的选项。
- 模型模式只显示 `evaluation_types` 包含 `model` 的选项。
- 已安装但未收录的自定义模型在两种模式都显示。
- 选择已安装项后立即更新当前模型并关闭面板。
- 选择未安装项后立即更新当前模型并关闭面板，随后由现有 App 流程弹出下载确认。
- 当前模型因切换评测类型而不再适用时，自动选择该模式下第一个已安装项；没有已安装项时选择
  第一个推荐项并保持提交阻塞，直到下载成功。

## Visual Direction

组件延续当前 EvalHub 蓝灰实验台视觉，不改变页面字体或全局配色。设计重点只花在模型选择器：

- 触发器使用浅灰白底、细蓝灰边框和清晰的展开状态，不使用大面积渐变。
- 模型 ID 使用等宽小字，友好名称保持正文无衬线字体。
- 能力标签使用低饱和蓝、紫、青三类短标签，编码真实用途而非装饰。
- 已安装项使用绿色圆点和“已安装”，推荐项使用下载图标和“约”容量。
- 当前项通过左侧 3px 蓝色能力轨与勾选图标识别，这是组件唯一的视觉签名。
- 面板限制高度并滚动；窄屏与触发器同宽，桌面端保持不超过表单列宽。
- 键盘焦点使用明显轮廓，`Escape` 关闭，点击组件外部关闭；减少动态效果偏好下不使用过渡位移。

## Accessibility

- 触发器是原生 `button`，使用 `aria-haspopup="listbox"`、`aria-expanded` 和关联面板 ID。
- 面板使用 `role="listbox"`，选项使用 `role="option"` 和 `aria-selected`。
- `ArrowDown`、`ArrowUp`、`Home`、`End` 移动活动项，`Enter` 或空格选择，`Escape` 关闭并把焦点
  返回触发器。
- 鼠标、触摸和键盘走同一个选择回调。
- 安装状态不只依赖颜色，始终有文本标签。
- 原有字段错误继续通过 `model-error` 与选择器关联。

## Error and Empty States

- Ollama 状态暂不可用且只有兜底模型时，组件仍展示当前值和“状态未知”。
- 当前模式没有任何选项时，禁用触发器并显示“暂无可用模型”。
- 未安装项被选中后继续显示“先下载模型或选择已安装模型”和“前往资产管理”。
- 下载失败、取消和进度展示继续由现有 App 与资产管理逻辑处理，不复制到选择器内部。

## Testing

### Backend

- 推荐目录包含扩充后的模型、准确容量、用途和能力标签。
- `_build_model_options` 保持已安装项优先、去重和实际容量优先。
- 自定义已安装模型默认支持两类评测。
- 已知不兼容的 `qwen2.5-coder:7b` 不包含 `agent` 用途。

### Frontend

- Agent 模式只展示 Agent 候选，模型模式展示答题候选。
- 分组、容量、能力和安装状态文本正确。
- 点击已安装模型更新选择；点击未安装模型继续触发现有下载确认。
- 切换模式时不保留不适用模型。
- 触发器和 listbox 支持键盘打开、移动、选择和关闭。
- 空列表与错误提示保持可访问。

## Compatibility and Non-goals

- API 只新增字段，不删除或重命名既有字段。
- 历史任务中的任意模型名继续正常展示。
- 本次不自动探测模型真实工具调用协议；目录基于已验证结果和官方能力声明。
- 本次不自动下载模型、不并发跑模型预评测，也不引入通用 Combobox 组件库。
