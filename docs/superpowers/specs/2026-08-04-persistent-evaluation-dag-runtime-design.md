# EvalHub 持久化评测 DAG Runtime 设计

## 目标

在现有 SQLite 任务中心和单 Worker 之上，把一次评测拆成可观测、可重试、可断点续跑的节点流程。用户能够查看每个节点的状态、耗时、尝试次数、输入输出和错误时间线；Benchmark 节点按样本即时落库，服务或模型中断后不重复运行已经成功的样本。

本次只实现本地单机 Runtime。继续使用 Python 标准库 `sqlite3`，不引入 SQLAlchemy、Alembic、消息队列或外部工作流引擎。

## 与现有任务中心的关系

- 保留 `evaluation_tasks` 作为一次完整评测的顶层记录、FIFO 排队单位和资源统计归属。
- 保留现有单 Worker，同一时间最多执行一个节点，不创建第二套调度线程。
- 保留同步 CLI 和 `/api/evaluations/run` 兼容入口；节点化只作用于异步任务 API。
- 异步请求新增 `suite_id`。旧请求只有 `dataset` 时自动生成只含该 Benchmark 的 `single-benchmark` Suite，默认仍运行完整数据集。
- 定时调度器只负责按计划创建普通评测任务，不参与节点执行；其接口和策略由已有调度器规格负责。

## 系统生成的流程

Runtime 根据提交时冻结的 Benchmark Suite 自动生成流程，首版不提供 DAG 编辑器：

```text
prepare_assets
      |
      +--> benchmark:<benchmark_id> --+
      +--> benchmark:<benchmark_id> --+--> capability_aggregate --> workflow_finalize
      +--> benchmark:<benchmark_id> --+
```

- `prepare_assets`：校验并准备 Suite 所需的真实数据集资产。
- `benchmark:<benchmark_id>`：每个 Benchmark 一个节点；样本是该节点内部的检查点，不是 DAG 节点。
- `capability_aggregate`：读取所有成功 Benchmark 的结果，生成行业最佳实践定义的六维 `CapabilityProfile` JSON。
- `workflow_finalize`：根据各节点终态更新顶层任务状态和最终摘要。

`capability_aggregate` 等待所有 Benchmark 进入终态后执行。存在失败或阻塞的 Benchmark 时，它仍生成部分画像，并把未覆盖能力写成 `null` 和明确的覆盖率，不能用 `0` 冒充低能力。`workflow_finalize` 只有在所有必需 Benchmark 成功时才把任务标记为 `success`；否则顶层任务为 `failed`，但部分画像仍可查看。

普通节点要求全部依赖成功；依赖失败或阻塞时节点进入 `blocked`。只有两个系统节点使用终态屏障：`capability_aggregate` 在至少一个 Benchmark 成功且其余 Benchmark 都已终止时运行，`workflow_finalize` 在所有上游节点终止后运行。若没有任何 Benchmark 成功，画像节点进入 `blocked`，终结节点仍负责把顶层任务标记为 `failed`。

## 节点状态机

节点只使用六种持久化状态：

- `pending`：等待依赖满足或等待下一次尝试。
- `running`：当前 Worker 正在执行。
- `success`：节点产物已经完整提交。
- `failed`：瞬时错误已达到最多三次尝试，或发生未分类运行错误。
- `blocked`：数据、配置、评分或依赖错误，需要人工处理后重试。
- `canceled`：顶层任务被取消，节点不再执行。

允许的关键转换：

```text
pending -> running -> success
                   -> pending   自动重试
                   -> failed    重试耗尽或未知错误
                   -> blocked   确定性错误
pending/running -> canceled
failed/blocked -> pending       人工重试
```

“可运行”不单独存状态：节点为 `pending` 且依赖满足时由查询动态判定。每次状态转换必须在同一个 SQLite 事务中同时更新节点快照并插入审计事件。

## SQLite 数据模型

### `evaluation_nodes`

保存节点最新快照：

- `id`、`task_id`、`node_key`，并约束 `(task_id, node_key)` 唯一；
- `kind`、`depends_on_json`、`status`；
- `attempt_count`、`max_attempts`，默认最多三次；
- `input_json`，保存冻结后的数据集版本、模型和有效运行配置；
- `checkpoint_json`、`output_json`、`error_type`、`error_message`；
- `completed_samples`、`total_samples`；
- `created_at`、`updated_at`、`started_at`、`attempt_started_at`、`finished_at`、`elapsed_ms`。

`elapsed_ms` 累加已结束尝试的运行时间；运行中展示值为 `elapsed_ms + 当前尝试耗时`。依赖先用 JSON 数组保存，因为流程由系统生成且规模很小，不新增边表。

### `evaluation_node_events`

追加写入节点审计时间线：

- 自增 `id`、`task_id`、`node_id`、`created_at`；
- `event_type`、`from_status`、`to_status`、`attempt`；
- `actor`，本地首版使用 `system`、`worker` 或 `local_user`；
- `message`、`payload_json`。

事件用于记录创建、开始、检查点、自动重试、人工重试、成功、失败、阻塞、恢复和取消。只记录结构化里程碑和错误，不把模型逐 token 输出当日志写入数据库。成功事件保留本次产物快照；因此显式重试生成新产物时，旧尝试仍可审计。

### `evaluation_sample_results`

保存 Benchmark 节点的样本级检查点：

- `task_id`、`node_id`、`sample_key`，并约束 `(node_id, sample_key)` 唯一；
- `sample_index`、`status`、`attempt_count`；
- `input_json`、`result_json`、`last_error_json`；
- `created_at`、`updated_at`、`finished_at`。

每条样本评分完成后，在同一事务中写入样本结果并更新节点 `checkpoint_json` 和进度。恢复时以成功的 `sample_key` 为准跳过已有结果；失败或缺失样本继续执行。冻结的数据集版本和样本标识保证恢复期间输入不漂移。

数据库初始化继续使用 `CREATE TABLE IF NOT EXISTS`。本次只增加表和索引，不修改已有任务列，因此不需要迁移框架；同时启用 WAL、`busy_timeout` 和外键检查。

## 执行与恢复

1. 创建异步评测任务时，先在一个事务中写入顶层任务、全部节点和 `node_created` 事件，再返回 HTTP 202。
2. 单 Worker 只处理 FIFO 队首任务，从该任务中选择创建顺序最早的可运行节点，原子切换为 `running` 并增加尝试次数；当前任务终结前不穿插后续任务。
3. Benchmark 节点加载冻结的样本清单，跳过数据库中已经成功的 `sample_key`，逐条完成推理、评分和提交。
4. 节点成功后写入 `output_json` 和成功事件；Worker 随后选择下一个可运行节点。
5. 服务启动时检查遗留的 `running` 节点：未耗尽尝试次数的节点记一条恢复事件后回到 `pending`，否则标记 `failed`。已经提交的样本保持不变。

取消顶层任务时，当前子进程按现有机制终止；所有未完成节点转为 `canceled`，已成功节点和样本结果保留。

顶层任务从第一个节点启动起保持 `running`。其样本进度是所有 Benchmark 节点 `completed_samples / total_samples` 的合计，准备和聚合节点不伪造样本进度；资源峰值继续覆盖整次任务。正常终态由 `workflow_finalize` 写入；若终结节点自身异常，Runtime 直接把顶层任务标记为 `failed`，避免任务永久停留在运行态。

## 错误与重试

- 自动重试：连接中断、超时、HTTP 429/5xx、Ollama 临时不可用和评测子进程异常退出。节点回到 `pending`，最多尝试三次，每次都写审计事件。
- 人工处理：数据损坏、缺少字段、无效配置、评分器确定性错误和依赖阻塞。节点进入 `blocked`，不自动消耗更多资源。
- 未分类异常：记录安全错误摘要并进入 `failed`，避免无限循环。
- 人工重试只允许 `failed` 或 `blocked` 节点。重试该节点时同步把已成功的后继节点重置为 `pending`，保留历史事件；Benchmark 已成功样本默认复用。

错误分类集中在一个 Runtime 边界函数中，执行器只抛出带类别的错误，不在各节点重复判断。

## 能力画像产物

`capability_aggregate` 只消费持久化 Benchmark 输出，不再次调用模型。其 `output_json` 至少包含：

- Suite 标识和版本、模型标识、生成时间；
- 知识、指令遵循、数学、综合推理、代码、安全可信六个固定能力轴的分数、覆盖率和参与聚合的 Benchmark；
- 未评测能力的 `null` 值及原因；
- 成功、失败和阻塞的 Benchmark 数量。

React 使用该 JSON 绘制六边形能力图。Runtime 不生成图片或 SVG，不耦合前端图表库。

## API 增量

保留现有任务 API，并增加节点信息：

- `POST /api/evaluations`：创建任务并生成节点。
- `GET /api/evaluations`：返回任务摘要。
- `GET /api/evaluations/{task_id}`：返回任务详情和节点摘要列表。
- `GET /api/evaluations/{task_id}/nodes/{node_id}`：返回节点输入输出、检查点、样本摘要和审计时间线。
- `GET /api/evaluations/{task_id}/nodes/{node_id}/samples?status=&cursor=&limit=`：分页读取样本结果，`limit` 默认 50、最大 200。
- `POST /api/evaluations/{task_id}/nodes/{node_id}/retry`：人工重试失败或阻塞节点。
- `POST /api/evaluations/{task_id}/cancel`：取消整个任务。

节点详情默认只返回样本计数和最近失败样本；完整样本通过分页端点按需读取，避免把全量数据集一次传给前端。

## 前端范围

任务详情增加紧凑的节点列表，展示节点名称、状态、样本进度、累计耗时和尝试次数。选中节点后显示：

- 有效配置与输入输出；
- 按时间排序的状态和错误事件；
- 失败或阻塞原因；
- 符合条件时的“重试节点”操作。

`capability_aggregate` 成功后展示六边形能力图和覆盖率；部分画像必须明显标注“部分完成”。首版不提供拖拽 DAG、交互式调试终端、断点单步执行或节点配置编辑器。

## 测试与验收

最小必测行为：

- 节点状态更新与审计事件同事务提交，任一失败时均回滚；
- 服务重启后恢复遗留运行节点，并跳过已成功样本；
- 瞬时错误最多自动尝试三次，确定性错误进入 `blocked`；
- 样本唯一约束阻止重复结果，样本结果与检查点原子提交；
- 人工重试重置必要的后继节点，但不删除历史事件或成功样本；
- 单个 Benchmark 失败时仍能生成部分能力画像，未覆盖能力为 `null` 而不是 `0`；
- 全部必需节点成功、存在失败/阻塞、用户取消三种情况下，顶层任务终态正确；
- 节点 API 的 404、409、分页和错误结构稳定；
- 前端能展示节点状态、耗时、时间线、重试操作和部分能力画像。

## 非目标

- PostgreSQL、多 Worker、分布式锁、消息队列和外部工作流引擎；
- 用户自定义 DAG、节点插件系统和通用 Artifact Store；
- 交互式 Shell、远程断点和逐 token 日志；
- 对成功样本的强制全量重跑；
- 在本规格中实现定时调度器。
