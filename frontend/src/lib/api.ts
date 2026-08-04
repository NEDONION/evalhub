import type {
  BenchmarksResponse,
  DatasetName,
  DatasetsResponse,
  EvaluationRequest,
  EvaluationNodeDetail,
  EvaluationNodeSummary,
  EvaluationSamplesResponse,
  EvaluationResult,
  EvaluationTaskDetail,
  EvaluationTaskSummary,
  EvaluationType,
  HealthResponse,
  ModelPerformanceResponse,
  OllamaPullResponse,
  OllamaStatus,
  PrepareDatasetResponse,
  SuitesResponse,
} from "../types";

interface ErrorEnvelope {
  ok?: boolean;
  error?: string;
}

interface EvaluationResponse {
  ok: true;
  result: EvaluationResult;
}

interface EvaluationTaskResponse {
  ok?: true;
  task: EvaluationTaskDetail;
}

interface EvaluationTaskSummaryResponse {
  ok: true;
  task: EvaluationTaskSummary;
}

interface EvaluationTasksResponse {
  tasks: EvaluationTaskSummary[];
}

interface EvaluationNodeResponse {
  ok?: true;
  node: EvaluationNodeDetail;
}

interface EvaluationNodeSummaryResponse {
  ok: true;
  node: EvaluationNodeSummary;
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
  // 旧后端或静态回退会返回 HTML；先检查类型，避免暴露无意义的 JSON 语法错误。
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.toLowerCase().includes("json")) {
    await response.text();
    throw new ApiError("后端返回了非 JSON 响应，请重启 EvalHub 服务。", response.status);
  }

  let body: T & ErrorEnvelope;
  try {
    body = (await response.json()) as T & ErrorEnvelope;
  } catch {
    throw new ApiError("后端返回了无法解析的 JSON 响应。", response.status);
  }

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

/** 查询版本化 Benchmark Registry 及本地执行器就绪状态。 */
export function getBenchmarks(): Promise<BenchmarksResponse> {
  return fetchJson<BenchmarksResponse>("/api/benchmarks");
}

/** 查询系统内置的行业评测套件。 */
export function getSuites(): Promise<SuitesResponse> {
  return fetchJson<SuitesResponse>("/api/suites");
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

/**
 * 创建持久化异步评测任务，并立即返回排队摘要而不等待模型执行完成。
 *
 * @param request 已校验的模型或 Agent 评测配置。
 * @returns 新任务的排队状态、初始进度和空结果摘要。
 */
export async function createEvaluation(request: EvaluationRequest): Promise<EvaluationTaskSummary> {
  const response = await fetchJson<EvaluationTaskSummaryResponse>("/api/evaluations", {
    method: "POST",
    body: JSON.stringify(request),
  });
  return response.task;
}

/**
 * 查询按创建时间倒序排列的轻量任务历史，供任务中心和活动轮询使用。
 *
 * @returns 不包含完整原始结果的任务摘要列表。
 */
export async function getEvaluationTasks(): Promise<EvaluationTaskSummary[]> {
  const response = await fetchJson<EvaluationTasksResponse>("/api/evaluations");
  return response.tasks;
}

/**
 * 读取同一评测类型、Benchmark 或 Suite 范围内的基模历史排行与成绩轨迹。
 *
 * @param scope 可选的 `benchmark:<id>` 或 `suite:<id>`；省略时由服务端选择默认范围。
 * @param evaluationType 模型评测或 Agent 评测，两种评分协议由服务端严格隔离。
 * @returns 可用比较范围、当前排行榜和最新破纪录节点。
 */
export function getModelPerformance(
  scope?: string,
  evaluationType: EvaluationType = "model",
): Promise<ModelPerformanceResponse> {
  const query = new URLSearchParams();
  if (scope) query.set("scope", scope);
  if (evaluationType === "agent") query.set("evaluation_type", evaluationType);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return fetchJson<ModelPerformanceResponse>(`/api/model-performance${suffix}`);
}

/**
 * 按任务标识读取资源遥测、请求配置与可选完整评测结果。
 *
 * @param taskId 用户在任务列表中选择的持久化任务标识。
 * @returns 选中任务的完整详情。
 */
export async function getEvaluationTask(taskId: string): Promise<EvaluationTaskDetail> {
  const response = await fetchJson<EvaluationTaskResponse>(`/api/evaluations/${encodeURIComponent(taskId)}`);
  return response.task;
}

/**
 * 请求取消排队中或运行中的评测，并返回服务端确认后的终态详情。
 *
 * @param taskId 需要取消的活动任务标识。
 * @returns 已取消任务的最新完整详情。
 */
export async function cancelEvaluationTask(taskId: string): Promise<EvaluationTaskDetail> {
  const response = await fetchJson<EvaluationTaskResponse>(
    `/api/evaluations/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST" },
  );
  return response.task;
}

/** 读取一个任务节点的输入、检查点、输出和审计事件。 */
export async function getEvaluationNode(
  taskId: string,
  nodeId: string,
): Promise<EvaluationNodeDetail> {
  const response = await fetchJson<EvaluationNodeResponse>(
    `/api/evaluations/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}`,
  );
  return response.node;
}

/** 分页读取节点样本检查点，默认由调用方只拉取需要调试的失败样本。 */
export function getEvaluationNodeSamples(
  taskId: string,
  nodeId: string,
  options: { status?: "success" | "failed"; limit?: number; cursor?: string } = {},
): Promise<EvaluationSamplesResponse> {
  const query = new URLSearchParams();
  if (options.status) query.set("status", options.status);
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  if (options.cursor) query.set("cursor", options.cursor);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return fetchJson<EvaluationSamplesResponse>(
    `/api/evaluations/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/samples${suffix}`,
  );
}

/** 重置失败或阻塞节点及其后继，并把原任务重新放回 FIFO 队列。 */
export async function retryEvaluationNode(
  taskId: string,
  nodeId: string,
): Promise<EvaluationNodeSummary> {
  const response = await fetchJson<EvaluationNodeSummaryResponse>(
    `/api/evaluations/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/retry`,
    { method: "POST" },
  );
  return response.node;
}
