import type { AdapterType, DatasetName, EvaluationRequest, SampleMode } from "../types";

export interface EvaluationFormValues {
  dataset: DatasetName;
  subject: string;
  adapter: AdapterType;
  model: string;
  baseUrl: string;
  sampleMode: SampleMode;
  limit: string;
}

export function buildEvaluationRequest(values: EvaluationFormValues): EvaluationRequest {
  const request: EvaluationRequest = {
    dataset: values.dataset,
    adapter: values.adapter,
    model: values.model,
    base_url: values.baseUrl,
    sample_mode: values.sampleMode,
  };

  if (values.dataset === "mmlu") {
    request.subject = values.subject;
  }

  if (values.sampleMode === "custom") {
    request.limit = Number(values.limit);
  }

  return request;
}

export function validateEvaluation(values: EvaluationFormValues): Record<string, string> {
  if (values.sampleMode === "custom" && !/^[1-9]\d*$/.test(values.limit)) {
    return { limit: "样本数量必须是大于 0 的整数" };
  }
  return {};
}

export function formatScore(value: number): string {
  return value.toFixed(4);
}

export function formatPassRate(passed: number, total: number): string {
  if (total === 0) {
    return "0%";
  }
  return `${Math.round((passed / total) * 100)}%`;
}
