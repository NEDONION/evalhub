import { describe, expect, it } from "vitest";

import {
  buildEvaluationRequest,
  formatPassRate,
  formatScore,
  type EvaluationFormValues,
  validateEvaluation,
} from "./evaluation";

const baseValues: EvaluationFormValues = {
  dataset: "gsm8k",
  subject: "abstract_algebra",
  adapter: "ollama",
  model: "qwen2.5:0.5b",
  baseUrl: "http://127.0.0.1:11434",
  sampleMode: "all",
  limit: "20",
};

describe("evaluation form rules", () => {
  it("omits limit when running all samples", () => {
    expect(buildEvaluationRequest(baseValues)).not.toHaveProperty("limit");
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
