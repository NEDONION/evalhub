import type {
  DatasetName,
  DatasetsResponse,
  EvaluationRequest,
  EvaluationResult,
  HealthResponse,
  OllamaPullResponse,
  OllamaStatus,
  PrepareDatasetResponse,
} from "../types";

interface ErrorEnvelope {
  ok?: boolean;
  error?: string;
}

interface EvaluationResponse {
  ok: true;
  result: EvaluationResult;
}

export class ApiError extends Error {
  /**
   * 创建带 HTTP 状态码的可诊断请求错误。
   *
   * @param message 后端返回的稳定错误信息或客户端兜底文案。
   * @param status 原始 HTTP 状态码，供调用方区分输入错误和服务故障。
   */
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * 统一发送 JSON 请求、解析响应，并把非成功状态转换为 `ApiError`。
 *
 * @param url 同源 API 路径及已编码查询参数。
 * @param options Fetch 请求配置；调用方只需提供方法和正文。
 * @returns 后端 JSON 正文对应的强类型对象。
 * @throws {ApiError} 当 HTTP 状态不是成功或响应显式包含 `ok: false` 时抛出。
 */
async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  const body = (await response.json()) as T & ErrorEnvelope;

  if (!response.ok || body.ok === false) {
    throw new ApiError(body.error || `请求失败：${response.status}`, response.status);
  }

  return body;
}

/**
 * 查询 EvalHub 服务健康状态。
 *
 * @returns 服务标识和健康状态；请求失败时由统一边界抛出 `ApiError`。
 */
export function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>("/api/health");
}

/**
 * 查询全部公开 Benchmark 的本地缓存状态和样本数量。
 *
 * @returns 数据集目录响应；请求失败时由统一边界抛出 `ApiError`。
 */
export function getDatasets(): Promise<DatasetsResponse> {
  return fetchJson<DatasetsResponse>("/api/datasets");
}

/**
 * 查询目标模型对应的 Ollama 安装与运行状态。
 *
 * @param model 要探测的 Ollama 模型标签。
 * @param baseUrl 用户配置的 Ollama 服务根地址。
 */
export function getOllamaStatus(model: string, baseUrl: string): Promise<OllamaStatus> {
  const query = new URLSearchParams({ model, base_url: baseUrl });
  return fetchJson<OllamaStatus>(`/api/ollama/status?${query.toString()}`);
}

/**
 * 启动或幂等复用目标模型的服务端下载任务。
 *
 * @param model 要拉取的 Ollama 模型标签。
 * @param baseUrl 仅允许本机回环地址的 Ollama 服务根地址。
 */
export function startModelPull(model: string, baseUrl: string): Promise<OllamaPullResponse> {
  return fetchJson<OllamaPullResponse>("/api/ollama/pulls", {
    method: "POST",
    body: JSON.stringify({ model, base_url: baseUrl }),
  });
}

/**
 * 恢复指定模型最近一次下载任务快照，页面刷新后可继续展示进度。
 *
 * @param model 要查询的 Ollama 模型标签。
 */
export function getModelPull(model: string): Promise<OllamaPullResponse> {
  const query = new URLSearchParams({ model });
  return fetchJson<OllamaPullResponse>(`/api/ollama/pulls?${query.toString()}`);
}

/**
 * 请求取消目标模型的活动下载；不存在的任务由统一请求边界抛出 `ApiError`。
 *
 * @param model 要取消的 Ollama 模型标签。
 */
export function cancelModelPull(model: string): Promise<OllamaPullResponse> {
  const query = new URLSearchParams({ model });
  return fetchJson<OllamaPullResponse>(`/api/ollama/pulls?${query.toString()}`, {
    method: "DELETE",
  });
}

/**
 * 缓存或强制更新一个公开 Benchmark 数据集。
 *
 * @param dataset 数据集稳定名称。
 * @param force 已缓存数据集为 `true` 时重新下载、校验并替换本地资产。
 */
export function prepareDataset(dataset: DatasetName, force = false): Promise<PrepareDatasetResponse> {
  return fetchJson<PrepareDatasetResponse>("/api/datasets/prepare", {
    method: "POST",
    body: JSON.stringify({ dataset, force }),
  });
}

/**
 * 提交同步评测请求并解包后端的标准结果对象。
 *
 * @param request 已通过表单校验的 Benchmark、适配器、模型和样本配置。
 * @returns 后端生成的聚合指标与失败样本。
 */
export async function runEvaluation(request: EvaluationRequest): Promise<EvaluationResult> {
  const response = await fetchJson<EvaluationResponse>("/api/evaluations/run", {
    method: "POST",
    body: JSON.stringify(request),
  });
  return response.result;
}
