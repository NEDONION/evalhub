# EvalHub Frontend

蓝白色本地模型评测控制台，使用 Vite、React、TypeScript 和 Tailwind CSS 构建。

控制台通过异步任务 API 创建评测，并在任务活动期间每秒轮询紧凑摘要。任务列表展示状态、真实进度和耗时；选中任务后才请求完整结果与失败样例。CPU/内存/GPU 数据均由后端采集，前端不模拟资源数值。

## 安装依赖

在项目根目录执行：

```bash
npm --prefix frontend install
```

## 开发模式

先在一个终端启动 Python API：

```bash
./scripts/start_local.sh
```

再在另一个终端启动 Vite：

```bash
npm --prefix frontend run dev
```

浏览器访问 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 生产构建

```bash
npm --prefix frontend run build
./scripts/start_local.sh
```

Python 服务会从 `frontend/dist` 提供构建后的页面，访问地址为 `http://127.0.0.1:8000`。

## 页面目录

- `概览`：服务、Ollama、数据集和最近得分的就绪状态。
- `发起评测`：选择 Benchmark、模型适配器和样本范围；未下载的 Ollama 模型会阻止提交。
- `资产管理`：下载或取消推荐模型并查看大小、速度、进度和 ETA；首次缓存或强制更新公开数据集。
- `评测结果`：查看当前会话最近一次任务的运行状态、聚合指标和失败样本。

桌面端目录固定在左侧，移动端显示为可横向滚动的顶部目录。切换目录不会中断正在进行的下载或评测，也不会清空评测表单。

## 验证

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```
