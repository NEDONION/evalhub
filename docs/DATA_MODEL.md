# EvalHub Data Model

## Model

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | 模型 ID |
| name | string | 模型名称 |
| version | string | 版本 |
| type | enum | base/sft/rlhf/agent/api |
| endpoint | string? | API 模型地址 |
| checkpoint_path | string? | checkpoint 路径 |
| metadata | json | 附加信息 |
| created_at | datetime | 创建时间 |

## Dataset

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | 数据集 ID |
| name | string | 数据集名称 |
| version | string | 版本 |
| storage_uri | string | 存储地址 |
| schema | json | 样本结构 |
| owner | string | 负责人 |
| sample_count | int | 样本数量 |
| created_at | datetime | 创建时间 |

## Benchmark

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | Benchmark ID |
| name | string | 名称 |
| dataset_id | string | 绑定 Dataset |
| evaluator_type | string | Evaluator 插件名称 |
| config | json | 推理和评测配置 |
| created_at | datetime | 创建时间 |

## EvaluationJob

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | Job ID |
| model_id | string | 模型 ID |
| benchmark_id | string | Benchmark ID |
| status | enum | pending/running/success/failed/canceled |
| runtime_config | json | 运行参数 |
| created_at | datetime | 创建时间 |
| started_at | datetime? | 开始时间 |
| finished_at | datetime? | 结束时间 |
| error_message | string? | 错误信息 |

## EvaluationSampleResult

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | 结果 ID |
| job_id | string | Job ID |
| sample_id | string | 样本 ID |
| input | string | 输入 |
| prediction | string | 模型输出 |
| reference | string | 参考答案 |
| score | float | 分数 |
| metric | string | 指标名称 |
| reason | string? | Judge reason 或错误解释 |
| created_at | datetime | 创建时间 |

## EvaluationReport

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| job_id | string | Job ID |
| total_samples | int | 总样本数 |
| passed_samples | int | 通过样本数 |
| average_score | float | 平均分 |
| metric | string | 指标名称 |
| failed_sample_ids | list[string] | 失败样本 |
