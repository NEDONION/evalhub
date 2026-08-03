# Local Console Enterprise UI Design

## Goal

把 EvalHub 本地控制台打磨成中文企业级评测工作台，并让默认评测行为符合真实 Benchmark 使用方式：默认跑完整数据集，用户可切换为快速试跑或自定义样本数。

## Scope

- 前端采用蓝白企业 SaaS 风格，参考 Vercel 的克制视觉密度：白底、浅灰边框、清晰层级、低饱和蓝色强调色。
- 页面中文化，保留必要英文技术名词。
- 增加 Ollama 状态卡片，让用户能看到命令是否安装、服务是否运行、模型是否已下载。
- 一键启动脚本在发现 Ollama 未运行时尝试启动 `ollama serve`。
- CLI `run-benchmark` 不传 `--limit` 时跑完整数据集。

## Non-Goals

- 不引入 React/Vite/Ant Design，当前仍保持零前端构建依赖。
- 不做持久化任务历史。
- 不自动下载 Ollama 模型，避免误触大文件下载。

## Data Flow

前端调用 `/api/ollama/status` 展示本地模型服务状态。启动脚本负责尽力启动 Ollama，Web UI 只检测和提示，不在浏览器里直接启动本机进程。

评测表单使用 `sample_mode` 和 `limit` 表达样本规模。`sample_mode=all` 时不传限制，后端加载完整数据集；`quick` 固定 5 条；`custom` 使用用户输入。

## Validation

- 单元测试覆盖 Ollama 状态检测。
- 单元测试覆盖 CLI 参数默认全量。
- `node --check frontend/app.js` 验证前端脚本语法。
- `compileall` 验证 Python 语法。
