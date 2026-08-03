import type {
  DatasetName,
  DatasetsResponse,
  EvaluationRequest,
  EvaluationResult,
  HealthResponse,
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
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

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

export function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>("/api/health");
}

export function getDatasets(): Promise<DatasetsResponse> {
  return fetchJson<DatasetsResponse>("/api/datasets");
}

export function getOllamaStatus(model: string, baseUrl: string): Promise<OllamaStatus> {
  const query = new URLSearchParams({ model, base_url: baseUrl });
  return fetchJson<OllamaStatus>(`/api/ollama/status?${query.toString()}`);
}

export function prepareDataset(dataset: DatasetName): Promise<PrepareDatasetResponse> {
  return fetchJson<PrepareDatasetResponse>("/api/datasets/prepare", {
    method: "POST",
    body: JSON.stringify({ dataset }),
  });
}

export async function runEvaluation(request: EvaluationRequest): Promise<EvaluationResult> {
  const response = await fetchJson<EvaluationResponse>("/api/evaluations/run", {
    method: "POST",
    body: JSON.stringify(request),
  });
  return response.result;
}
