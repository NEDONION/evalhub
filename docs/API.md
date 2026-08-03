# EvalHub API Draft

这是后续 FastAPI 层的接口草案，当前代码先保留轻量 `health` 入口。

## Health

```http
GET /health
```

响应：

```json
{"status": "ok", "service": "evalhub"}
```

## Models

```http
POST /models
GET /models
GET /models/{model_id}
```

创建模型：

```json
{
  "name": "qwen-demo",
  "version": "v1.0",
  "type": "sft",
  "endpoint": "http://localhost:8001/v1/chat/completions"
}
```

## Datasets

```http
POST /datasets
GET /datasets
GET /datasets/{dataset_id}
```

## Benchmarks

```http
POST /benchmarks
GET /benchmarks
GET /benchmarks/{benchmark_id}
```

## Evaluation Jobs

```http
POST /evaluation-jobs
GET /evaluation-jobs
GET /evaluation-jobs/{job_id}
POST /evaluation-jobs/{job_id}/cancel
```

创建任务：

```json
{
  "model_id": "model_123",
  "benchmark_id": "benchmark_456",
  "runtime_config": {
    "temperature": 0,
    "max_tokens": 512
  }
}
```

## Results

```http
GET /evaluation-jobs/{job_id}/results
GET /evaluation-jobs/{job_id}/report
```

## Release Gates

```http
POST /release-gates
POST /release-gates/{gate_id}/evaluate
```
