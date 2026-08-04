export type DatasetName = string;
export type AdapterType = "ollama" | "oracle";
export type SampleMode = "all" | "quick" | "custom";
export type EvaluationType = "model" | "agent";
export type AgentFramework = "codex";
export type AgentDifficulty = "all" | "easy" | "medium" | "hard";

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
  size_bytes: number | null;
  size_kind: "actual" | "estimated" | "unknown";
}

export type PullStatus = "pending" | "pulling" | "verifying" | "success" | "failed" | "canceled";

export interface OllamaPullTask {
  model: string;
  status: PullStatus;
  message: string;
  completed_bytes: number | null;
  total_bytes: number | null;
  speed_bytes_per_second: number | null;
  eta_seconds: number | null;
  error: string | null;
}

export interface OllamaPullResponse {
  ok: true;
  task: OllamaPullTask | null;
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
  evaluation_type?: EvaluationType;
  agent_framework?: AgentFramework;
  agent_difficulty?: AgentDifficulty;
  dataset: DatasetName;
  adapter: AdapterType;
  model: string;
  base_url: string;
  sample_mode: SampleMode;
  subject?: string;
  limit?: number;
  suite_id?: string;
}

export type BenchmarkExecutor = "native" | "lm_eval" | "sandboxed_code";

export interface BenchmarkDefinition {
  id: string;
  version: string;
  display_name: string;
  capability: string;
  capability_label: string;
  dataset_source: string;
  dataset_revision: string;
  homepage: string;
  executor: BenchmarkExecutor;
  metric: string;
  locally_runnable: boolean;
  readiness_reason: string | null;
}

export interface BenchmarkSuite {
  id: string;
  version: string;
  display_name: string;
  benchmark_ids: string[];
  benchmark_count: number;
  locally_runnable_count: number;
}

export interface FailedExample {
  sample_id: string;
  difficulty?: Exclude<AgentDifficulty, "all">;
  difficulty_reason?: string;
  score: number;
  input: string;
  prediction: string;
  reference: string;
  reason: string | null;
}

export interface AgentCapabilityDimension {
  key: string;
  label: string;
  score: number;
}

export interface AgentCapabilityReport {
  overall_score: number;
  dimensions: AgentCapabilityDimension[];
}

export interface AgentRunMetadata {
  framework: AgentFramework;
  cli_version: string;
  scaffold_hash: string;
}

export interface AgentSampleResult {
  sample_id: string;
  difficulty: Exclude<AgentDifficulty, "all">;
  difficulty_reason: string;
  status: "success" | "failed";
  score: number;
  final_message: string;
  event_count: number;
  wall_time_seconds: number;
  verifier_message: string;
}

export interface AgentDifficultyResult {
  difficulty: Exclude<AgentDifficulty, "all">;
  total: number;
  passed: number;
  pass_rate: number;
}

export interface EvaluationResult {
  evaluation_type?: EvaluationType;
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
  benchmark_version?: string;
  requested_difficulty?: AgentDifficulty;
  difficulty_report?: AgentDifficultyResult[];
  agent?: AgentRunMetadata;
  capability_report?: AgentCapabilityReport;
  capability_profile?: ModelCapabilityProfile;
  sample_results?: AgentSampleResult[];
}

export interface ModelCapabilityBenchmarkResult {
  benchmark_id: string;
  display_name: string;
  status: string;
  raw_score?: number;
  normalized_score?: number;
  error_type?: string;
}

export interface ModelCapabilityDimension {
  label: string;
  score: number | null;
  status: "complete" | "partial" | "unassessed";
  coverage: number;
  benchmark_results: ModelCapabilityBenchmarkResult[];
}

export interface ModelCapabilityProfile {
  suite_id: string;
  suite_version: string;
  model: string | null;
  generated_at: string;
  status: "complete" | "partial" | "unassessed";
  counts: Record<string, number>;
  capabilities: Record<string, ModelCapabilityDimension>;
}

export type EvaluationTaskStatus = "pending" | "running" | "success" | "failed" | "canceled";

export interface EvaluationTaskProgress {
  completed_samples: number;
  total_samples: number;
  percent: number;
}

export interface EvaluationTaskTiming {
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
}

export interface EvaluationTaskResources {
  cpu: {
    current_percent: number;
    peak_percent: number;
  };
  memory: {
    current_bytes: number;
    peak_bytes: number;
  };
  gpu: {
    supported: boolean;
    current_percent: number | null;
    peak_percent: number | null;
    current_memory_bytes: number | null;
    peak_memory_bytes: number | null;
  };
}

export interface EvaluationTaskResultSummary {
  benchmark: string;
  total_samples: number;
  passed_samples: number;
  average_score: number;
}

export interface EvaluationTaskSummary {
  id: string;
  status: EvaluationTaskStatus;
  evaluation_type?: EvaluationType;
  agent_framework?: AgentFramework | null;
  dataset: DatasetName;
  suite_id?: string | null;
  model: string;
  adapter: AdapterType;
  progress: EvaluationTaskProgress;
  timing: EvaluationTaskTiming;
  resources: EvaluationTaskResources;
  result_summary: EvaluationTaskResultSummary | null;
  error_message: string | null;
}

export interface EvaluationTaskDetail extends EvaluationTaskSummary {
  request: EvaluationRequest;
  result: EvaluationResult | null;
  nodes?: EvaluationNodeSummary[];
}

export type EvaluationNodeStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "blocked"
  | "canceled";

export interface EvaluationNodeSummary {
  id: string;
  task_id: string;
  node_key: string;
  kind: string;
  depends_on: string[];
  status: EvaluationNodeStatus;
  attempt: { count: number; max: number };
  progress: EvaluationTaskProgress;
  timing: {
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    elapsed_ms: number;
  };
  error: { type: string | null; message: string | null } | null;
}

export interface EvaluationNodeEvent {
  id: number;
  event_type: string;
  from_status: EvaluationNodeStatus | null;
  to_status: EvaluationNodeStatus | null;
  attempt: number;
  actor: string;
  message: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface EvaluationNodeDetail extends EvaluationNodeSummary {
  input: Record<string, unknown>;
  checkpoint: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  events: EvaluationNodeEvent[];
}

export interface EvaluationSampleCheckpoint {
  task_id: string | null;
  node_id: string;
  sample_key: string;
  sample_index: number;
  status: "success" | "failed";
  attempt_count: number;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  last_error: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface DatasetsResponse {
  datasets: Dataset[];
}

export interface BenchmarksResponse {
  benchmarks: BenchmarkDefinition[];
}

export interface SuitesResponse {
  suites: BenchmarkSuite[];
}

export interface EvaluationSamplesResponse {
  samples: EvaluationSampleCheckpoint[];
  next_cursor: string | null;
}

export interface PrepareDatasetResponse {
  ok: true;
  dataset: DatasetName;
  path: string;
  operation: "cached" | "updated";
  sample_count: number;
}
