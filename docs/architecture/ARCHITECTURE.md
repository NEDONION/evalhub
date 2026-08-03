# EvalHub Architecture

## 架构目标

EvalHub 的架构目标是把评测平台拆成稳定的领域核心和可替换的基础设施。核心层不直接依赖 FastAPI、Celery、PostgreSQL 或 MinIO，这样 MVP 可以先用内存实现跑通，后续再替换为企业级组件。

## 分层设计

```text
React Console / CLI
        |
FastAPI API Layer
        |
Application Services
        |
Domain Model
        |
Infrastructure Adapters
```

当前代码先实现 Domain Model、Evaluator Plugin、Model Adapter 和同步 Runner。

## 目标生产架构

```text
React Console
     |
FastAPI
     |
Scheduler
     |
RabbitMQ
     |
Celery Worker
     |
+------------------+------------------+
|                                     |
Model Adapter                    Evaluator
|                                     |
Inference Endpoint               Result Store
                                      |
                              PostgreSQL / MinIO
```

## 核心模块

- `evalhub.domain`：实体、枚举和仓储协议。
- `evalhub.adapters`：模型推理适配器接口和具体实现。
- `evalhub.evaluators`：评测器接口、插件注册表和指标实现。
- `evalhub.engine`：评测任务执行、样本级结果生成、报告聚合。
- `evalhub.registry`：Registry 的内存实现，后续可替换为数据库实现。
- `evalhub.api`：FastAPI 入口，当前作为可选依赖模块。

## 扩展原则

新增模型类型时，实现 `ModelAdapter.generate()`。

新增评测指标时，实现 `Evaluator.evaluate()` 并注册到 `EvaluatorRegistry`。

新增结果存储时，实现 Repository 协议，不修改 Runner 主流程。

新增异步能力时，将 Runner 放进 Worker，API 只负责任务创建和状态查询。
