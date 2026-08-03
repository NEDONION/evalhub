# EvalHub Roadmap

## Phase 1: Core MVP

目标：跑通模型评测闭环。

- Python 包工程化
- Model Adapter 抽象
- Evaluator Plugin 抽象
- 内存 Registry
- 同步 Evaluation Runner
- 样本级 Result
- JSON Report
- CLI 示例

验收标准：一个模型、一个 Benchmark、一个 Dataset 可以产出 report。

## Phase 2: Backend Platform

目标：变成可服务化的后端平台。

- FastAPI CRUD API
- PostgreSQL Schema
- SQLAlchemy Repository
- Alembic Migration
- API 认证占位
- Report 查询 API

验收标准：通过 HTTP 创建 Model/Dataset/Benchmark/Job，并查询结果。

## Phase 3: Async Evaluation

目标：支持大规模评测任务。

- Celery Worker
- RabbitMQ
- Job Retry
- 并发控制
- Worker 日志
- 任务取消

验收标准：API 提交任务后异步执行，状态可追踪。

## Phase 4: Enterprise Features

目标：接近企业内部评测平台。

- LLM-as-a-Judge
- Leaderboard
- Release Gate
- Agent Evaluation
- MinIO Artifact Storage
- 权限和审计日志
- 成本追踪

验收标准：模型上线可以依赖 EvalHub 自动化评测和门禁判断。
