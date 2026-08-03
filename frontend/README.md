# EvalHub Frontend

蓝白色本地模型评测控制台，使用 Vite、React、TypeScript 和 Tailwind CSS 构建。

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

## 验证

```bash
npm --prefix frontend run test:run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```
