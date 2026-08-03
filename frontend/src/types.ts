export type DatasetName = "gsm8k" | "mmlu";
export type AdapterType = "ollama" | "oracle";
export type SampleMode = "all" | "quick" | "custom";

export interface Dataset {
  name: DatasetName;
  display_name: string;
  task_type: string;
  evaluator_type: string;
  homepage: string;
  source_url: string;
  local_path: string;
  description: string;
  prepared: boolean;
  sample_count: number | null;
}

export interface ModelOption {
  name: string;
  label: string;
  description: string;
  installed: boolean;
}

export interface OllamaStatus {
  installed: boolean;
  running: boolean;
  model_present: boolean;
  command: string | null;
  base_url: string;
  model: string;
  models: string[];
  model_options: ModelOption[];
  message: string;
}

export interface EvaluationRequest {
  dataset: DatasetName;
  adapter: AdapterType;
  model: string;
  base_url: string;
  sample_mode: SampleMode;
  subject?: string;
  limit?: number;
}

export interface FailedExample {
  sample_id: string;
  score: number;
  input: string;
  prediction: string;
  reference: string;
  reason: string | null;
}

export interface EvaluationResult {
  job_id: string;
  status: "pending" | "running" | "success" | "failed" | "canceled" | string;
  dataset: DatasetName;
  benchmark: string;
  model: string;
  adapter: AdapterType;
  metric: string;
  total_samples: number;
  passed_samples: number;
  average_score: number;
  failed_sample_ids: string[];
  failed_examples: FailedExample[];
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface DatasetsResponse {
  datasets: Dataset[];
}

export interface PrepareDatasetResponse {
  ok: true;
  dataset: DatasetName;
  path: string;
}
