import { describe, expect, it } from "vitest";

import {
  buildEvaluationRequest,
  formatPassRate,
  formatScore,
  type EvaluationFormValues,
  validateEvaluation,
} from "./evaluation";

const baseValues: EvaluationFormValues = {
  evaluationType: "model",
  agentFramework: "pi",
  dataset: "gsm8k",
  subject: "abstract_algebra",
  adapter: "ollama",
  model: "qwen2.5:0.5b",
  baseUrl: "http://127.0.0.1:11434",
  sampleMode: "all",
  agentDifficulty: "all",
  limit: "20",
  suiteId: null,
  providerId: null,
};

it("builds the fixed Pi Coding Mini request for Agent evaluation", () => {
  expect(
    buildEvaluationRequest({
      ...baseValues,
      evaluationType: "agent",
      dataset: "mmlu",
      adapter: "oracle",
      sampleMode: "custom",
      agentDifficulty: "hard",
      limit: "99",
    }),
  ).toEqual({
    evaluation_type: "agent",
    agent_framework: "pi",
    dataset: "coding_mini",
    adapter: "ollama",
    model: baseValues.model,
    base_url: baseValues.baseUrl,
    sample_mode: "all",
    agent_difficulty: "hard",
  });
});

it("builds a MiniClaw request without EvalHub model configuration", () => {
  const request = buildEvaluationRequest({
    ...baseValues,
    evaluationType: "agent",
    agentFramework: "miniclaw",
    agentDifficulty: "all",
  });

  expect(request).toEqual({
    evaluation_type: "agent",
    agent_framework: "miniclaw",
    dataset: "coding_mini",
    sample_mode: "all",
    agent_difficulty: "all",
  });
  expect(request).not.toHaveProperty("model");
  expect(request).not.toHaveProperty("adapter");
  expect(request).not.toHaveProperty("base_url");
});

describe("evaluation form rules", () => {
  it("omits limit when running all samples", () => {
    expect(buildEvaluationRequest(baseValues)).not.toHaveProperty("limit");
  });

  it("includes the selected industry suite without replacing the dataset fallback", () => {
    expect(
      buildEvaluationRequest({ ...baseValues, suiteId: "llm-industry-core-v1" }),
    ).toMatchObject({
      dataset: "gsm8k",
      suite_id: "llm-industry-core-v1",
    });
  });

  it("does not leak a single MMLU subject into an industry suite request", () => {
    const request = buildEvaluationRequest({
      ...baseValues,
      dataset: "mmlu",
      subject: "abstract_algebra",
      suiteId: "llm-industry-core-v1",
    });

    expect(request).not.toHaveProperty("subject");
  });

  it("leaves the quick sample limit to the backend", () => {
    expect(buildEvaluationRequest({ ...baseValues, sampleMode: "quick" })).toMatchObject({
      sample_mode: "quick",
    });
    expect(buildEvaluationRequest({ ...baseValues, sampleMode: "quick" })).not.toHaveProperty("limit");
  });

  it("converts a custom sample limit to a number", () => {
    expect(buildEvaluationRequest({ ...baseValues, sampleMode: "custom", limit: "20" })).toMatchObject({
      sample_mode: "custom",
      limit: 20,
    });
  });

  it("builds an API provider request without any credential field", () => {
    const request = buildEvaluationRequest({
      ...baseValues,
      adapter: "openai-compatible",
      providerId: "deepseek",
      model: "deepseek-v4-pro",
      baseUrl: "https://api.deepseek.com",
    });

    expect(request).toMatchObject({
      adapter: "openai-compatible",
      provider_id: "deepseek",
      model: "deepseek-v4-pro",
      base_url: "https://api.deepseek.com",
    });
    expect(request).not.toHaveProperty("api_key");
  });

  it("requires a provider and model for API evaluation", () => {
    expect(
      validateEvaluation({
        ...baseValues,
        adapter: "openai-compatible",
        providerId: null,
        model: "",
      }),
    ).toEqual({
      provider: "请选择模型服务商",
      model: "请输入模型 ID",
    });
  });

  it("rejects a non-positive custom sample limit", () => {
    expect(validateEvaluation({ ...baseValues, sampleMode: "custom", limit: "0" })).toEqual({
      limit: "样本数量必须是大于 0 的整数",
    });
  });

  it("includes a subject only for MMLU", () => {
    expect(buildEvaluationRequest({ ...baseValues, dataset: "mmlu" })).toHaveProperty(
      "subject",
      "abstract_algebra",
    );
    expect(buildEvaluationRequest(baseValues)).not.toHaveProperty("subject");
  });

  it("formats scores to four decimal places", () => {
    expect(formatScore(0.8)).toBe("0.8000");
  });

  it("formats pass rates and handles an empty result", () => {
    expect(formatPassRate(3, 4)).toBe("75%");
    expect(formatPassRate(0, 0)).toBe("0%");
  });
});
