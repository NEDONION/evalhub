# Local Evaluation Scheduler Design

## Goal

为 EvalHub 增加本地可持久化的评测调度与定时任务能力，同时定义可替换的调度接口，为后续 Celery/Redis、PostgreSQL 或 Temporal 分布式部署保留边界。

## Product Principles

- 本地版本保持一键启动，不强制用户安装 Redis 或 PostgreSQL。
- 调度对象是完整评测请求，不是任意 Python 函数。
- 定时触发产生独立 Evaluation Job，每次运行都可审计、可重试、可比较。
- 时间规则必须显式携带时区，默认 `Asia/Shanghai`。
- 重启服务后计划、下次执行时间和运行历史不能丢失。
- 调度器不得绕过 Benchmark 的数据、执行器和安全检查。

## Scope

- 支持手动立即触发已有计划，以及单次定时、固定间隔和 Cron 三类持久触发方式。
- 支持创建、编辑、暂停、恢复、删除和手动触发计划。
- 使用 SQLite 保存计划、触发记录和 Evaluation Job 关联。
- 支持并发限制、超时、重试、misfire 策略和重复执行保护。
- 提供调度中心 API 与中文企业级 UI。
- 定义 `Scheduler` 与 `JobDispatcher` 接口，允许替换为分布式实现。

## Non-Goals

- 第一版不提供多节点高可用调度。
- 第一版不实现跨租户资源配额和 RBAC。
- 第一版不发送邮件、Slack 或企业微信通知。
- 第一版不允许用户提交任意 shell 命令或 Python 代码作为定时任务。

## Architecture

本地实现由四个边界清晰的模块组成：

```text
Schedule API / UI
  -> Schedule Repository (SQLite)
  -> Local Scheduler (APScheduler)
  -> Evaluation Job Dispatcher
  -> Existing Evaluation Pipeline
  -> Schedule Run History
```

### Schedule Repository

SQLite 数据库存放在 `.runtime/evalhub.db`。Repository 负责计划定义、版本更新和运行历史，不把持久化细节暴露给 API。

### Local Scheduler

第一版采用稳定的 APScheduler 3.x 触发器语义，使用显式时区。调度器只计算触发和调用 `JobDispatcher`，不包含评测业务逻辑。

关键运行策略：

- 每个计划默认 `max_instances=1`，避免同一计划重叠执行。
- 默认 `coalesce=true`，服务离线期间错过多次触发时合并为一次。
- 默认 `misfire_grace_time=300` 秒，超过窗口则记录 `missed`，不补跑。
- 调度器重启后从 SQLite 恢复启用计划并重新计算 `next_run_at`。

### Job Dispatcher

`JobDispatcher` 接受版本化的 `EvaluationRequest` 快照，创建新的 Evaluation Job，并返回 `job_id`。它不直接执行用户代码。

```python
class JobDispatcher(Protocol):
    def dispatch(self, request: EvaluationRequest, *, trigger_id: str) -> str: ...
```

本地实现使用进程内有界队列和单 Worker。分布式实现可以替换为 Celery 或 Temporal，但必须保持相同输入快照和幂等键语义。

## Domain Model

### EvaluationSchedule

- `id`、`name`、`description`。
- `state`：`active`、`paused`、`error`、`deleted`。
- `created_at`、`updated_at`、`deleted_at`。
- `trigger_type`：`once`、`interval`、`cron`。
- `timezone`：IANA 时区字符串。
- `trigger_config`：执行时间、间隔或 Cron 字段。
- `evaluation_request`：模型、套件/Benchmark、全量或显式样本范围和运行参数快照。
- `max_concurrency`：第一版固定允许值 `1`。
- `timeout_seconds`。
- `retry_policy`：最大重试次数和固定退避秒数。
- `misfire_policy`：`coalesce`、宽限时间和是否补跑。
- `next_run_at`、`last_run_at`。
- `version`：乐观锁版本，防止并发编辑覆盖。

### ScheduleRun

- `id`、`schedule_id`、`schedule_version`。
- `scheduled_for`、`triggered_at`、`finished_at`。
- `status`：`queued`、`running`、`success`、`partial`、`failed`、`missed`、`canceled`。
- `attempt`、`idempotency_key`。
- `evaluation_job_id`、`error_code`、`error_message`。

`idempotency_key` 由 `schedule_id + scheduled_for` 生成并建立唯一约束，防止进程重启或重复回调生成两次相同运行。

## Trigger Semantics

- 手动立即执行作用于已有计划，通过触发接口产生带有该 `schedule_id` 的 `ScheduleRun`，不会改变原触发规则。
- 单次计划成功入队后自动禁用，失败重试仍属于同一次计划运行。
- 固定间隔以秒、分钟、小时或天配置，保存标准化秒数。
- Cron 使用五字段分钟级语义，UI 通过结构化控件生成表达式并同时展示自然语言说明。
- 所有 `next_run_at` 和历史时间以 UTC 保存，API 根据计划时区返回展示值。

## API

- `GET /api/schedules`：计划列表、状态、下次和最近执行时间。
- `POST /api/schedules`：创建并校验计划。
- `GET /api/schedules/{id}`：计划定义和最近运行。
- `PUT /api/schedules/{id}`：携带 `version` 更新计划。
- `DELETE /api/schedules/{id}`：软删除并取消未来触发，不删除历史。
- `POST /api/schedules/{id}/pause`：暂停。
- `POST /api/schedules/{id}/resume`：恢复并重新计算下次触发。
- `POST /api/schedules/{id}/trigger`：立即生成一次运行。
- `GET /api/schedule-runs`：按计划、状态和时间筛选运行历史。

无效 Cron、过去的单次时间、未知模型或套件在保存计划时返回结构化校验错误。执行器当前不可用属于可恢复环境状态：计划可以保存，但 API 返回警告；实际触发时若仍不可用，则创建失败运行记录并进入重试策略。

## Scheduler UI

侧栏新增“调度中心”，包含：

- 计划列表：名称、模型、套件、触发规则、状态、下次运行、最近结果和快捷暂停/恢复。
- 新建/编辑抽屉：评测请求、触发类型、时区、超时、重试和冲突策略。
- 运行历史：计划运行与 Evaluation Job 的关联、耗时、状态和报告入口。
- 顶部状态：调度器是否运行、队列长度、运行中任务数和失败任务数。

表单默认选择完整数据集。选择行业核心套件时，UI 显示完整运行可能耗时较长，但不自动缩减样本。

## Failure and Recovery

- 服务启动时读取启用计划，校验后恢复；无法恢复的计划标记 `error` 并保留定义。
- Ollama 或执行器未就绪时生成失败运行记录，按计划重试策略处理，不修改计划定义。
- Worker 崩溃后，超过租约时间的 `running` 记录转为 `failed`，不会自动宣称成功。
- 删除或暂停计划只影响未来触发，已经运行的 Evaluation Job 继续执行，除非用户另行取消任务。
- 数据库写入使用事务；ScheduleRun 与幂等键先持久化，再分发 Evaluation Job。
- 数据库文件损坏时启动失败并给出备份路径，不自动覆盖原文件。

## Security

- 定时任务只能引用 Registry 中的模型、Benchmark 和 Suite。
- API 不接受 shell 命令、文件路径执行或任意 Python callable。
- 代码 Benchmark 仍必须经过 `SandboxedCodeExecutor`，调度器没有绕过开关。
- 错误日志隐藏凭证和完整 Authorization header。

## Testing

- Trigger 单元测试使用注入时钟验证单次、间隔、Cron、时区和夏令时边界，不使用真实等待。
- Repository 测试使用临时 SQLite 验证重启恢复、事务和乐观锁冲突。
- 幂等测试对同一 `schedule_id + scheduled_for` 触发两次，只生成一个 ScheduleRun。
- 并发测试验证同一计划运行中时不会启动第二个实例。
- misfire 测试覆盖宽限内补跑、超过宽限记录 missed 和 coalesce。
- 重试测试验证次数、固定退避和最终状态。
- API 测试覆盖 CRUD、暂停/恢复、手动触发和结构化校验错误。
- 集成测试启动临时调度器，使用 Oracle 适配器产生真实 Evaluation Job 并验证历史关联。
- 前端测试覆盖中文状态、结构化 Cron 控件和窄屏无重叠。

## Distributed Migration Boundary

未来分布式部署替换 `LocalScheduler` 和 `LocalJobDispatcher`，保留以下契约不变：

- `EvaluationSchedule`、`ScheduleRun` 和 `EvaluationRequest` 字段语义。
- 幂等键、状态机和错误代码。
- Scheduler API 与前端调用方式。
- 评测结果和能力报告结构。

迁移到 Celery/Redis 或 Temporal 时，SQLite Repository 可替换为 PostgreSQL Repository，而不修改 Benchmark Registry、评测执行器和报告层。

## Rollout

1. 增加 SQLite Repository、调度领域模型和 Trigger 校验。
2. 增加 LocalScheduler、JobDispatcher、幂等和恢复流程。
3. 增加调度 API、启动生命周期和健康状态。
4. 增加调度中心 UI 与运行历史入口。
5. 在后续基础设施阶段实现 Celery/Redis 或 Temporal 适配器。
