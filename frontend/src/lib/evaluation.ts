import type {
  AgentDifficulty,
  AgentFramework,
  DatasetName,
  EvaluationRequest,
  EvaluationType,
  ModelAdapterType,
  SampleMode,
} from "../types";

export interface EvaluationFormValues {
  evaluationType: EvaluationType;
  agentFramework: AgentFramework;
  dataset: DatasetName;
  subject: string;
  adapter: ModelAdapterType;
  model: string;
  baseUrl: string;
  sampleMode: SampleMode;
  agentDifficulty: AgentDifficulty;
  limit: string;
  suiteId: string | null;
  providerId: string | null;
}

/**
 * 把页面表单状态转换为后端任务请求，并在 Agent 模式锁定首版可执行组合。
 *
 * @param values 用户当前可见的模型、数据集、样本范围和 Agent 难度表单值。
 * @returns 模型模式保留用户选项；Agent 模式固定运行环境并保留用户选择的难度。
 */
export function buildEvaluationRequest(values: EvaluationFormValues): EvaluationRequest {
  if (values.evaluationType === "agent") {
    if (values.agentFramework === "miniclaw") {
      return {
        evaluation_type: "agent",
        agent_framework: "miniclaw",
        dataset: "coding_mini",
        sample_mode: "all",
        agent_difficulty: values.agentDifficulty,
      };
    }

    // Pi 继续由 EvalHub 冻结模型和地址，保证旧任务请求与历史结果完全兼容。
    return {
      evaluation_type: "agent",
      agent_framework: "pi",
      dataset: "coding_mini",
      adapter: "ollama",
      model: values.model,
      base_url: values.baseUrl,
      sample_mode: "all",
      agent_difficulty: values.agentDifficulty,
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

  if (values.adapter === "openai-compatible" && values.providerId) {
    request.provider_id = values.providerId;
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
  const errors: Record<string, string> = {};
  if (values.sampleMode === "custom" && !/^[1-9]\d*$/.test(values.limit)) {
    errors.limit = "样本数量必须是大于 0 的整数";
  }
  if (values.adapter === "openai-compatible") {
    if (!values.providerId) errors.provider = "请选择模型服务商";
    if (!values.model.trim()) errors.model = "请输入模型 ID";
  }
  return errors;
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
