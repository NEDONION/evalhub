import type { AdapterType, DatasetName, EvaluationRequest, EvaluationType, SampleMode } from "../types";

export interface EvaluationFormValues {
  evaluationType: EvaluationType;
  dataset: DatasetName;
  subject: string;
  adapter: AdapterType;
  model: string;
  baseUrl: string;
  sampleMode: SampleMode;
  limit: string;
  suiteId: string | null;
}

/**
 * 把页面表单状态转换为后端任务请求，并在 Agent 模式锁定首版可执行组合。
 *
 * @param values 用户当前可见的模型、数据集和样本范围表单值。
 * @returns 模型模式保留用户选项；Agent 模式固定为 Codex、Coding Mini 和 Ollama。
 */
export function buildEvaluationRequest(values: EvaluationFormValues): EvaluationRequest {
  if (values.evaluationType === "agent") {
    return {
      evaluation_type: "agent",
      agent_framework: "codex",
      dataset: "coding_mini",
      adapter: "ollama",
      model: values.model,
      base_url: values.baseUrl,
      sample_mode: "quick",
    };
  }

  const request: EvaluationRequest = {
    evaluation_type: "model",
    dataset: values.dataset,
    adapter: values.adapter,
    model: values.model,
    base_url: values.baseUrl,
    sample_mode: values.sampleMode,
  };

  if (values.suiteId) {
    request.suite_id = values.suiteId;
  }

  if (!values.suiteId && values.dataset === "mmlu") {
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
