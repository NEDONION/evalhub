import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { EvaluationResult, ModelCapabilityProfile } from "../../types";
import { CAPABILITY_ORDER } from "../../lib/capabilities";
import { EvaluationResultDetail } from "./EvaluationResultDetail";

const profile: ModelCapabilityProfile = {
  suite_id: "llm-industry-core-v1",
  suite_version: "1.0.0",
  model: "qwen2.5:0.5b",
  generated_at: "2026-08-04T02:00:00+00:00",
  status: "partial",
  counts: { success: 2, failed: 0, blocked: 11 },
  capabilities: Object.fromEntries(
    CAPABILITY_ORDER.map((key, index) => [
      key,
      {
        label: key,
        score: index === 0 || index === 2 ? 80 : null,
        status: index === 0 || index === 2 ? "partial" : "unassessed",
        coverage: index === 0 || index === 2 ? 0.5 : 0,
        benchmark_results: [],
      },
    ]),
  ),
};

const result: EvaluationResult = {
  job_id: "job-profile",
  status: "partial",
  dataset: "gsm8k",
  benchmark: "LLM 行业核心套件 v1",
  model: "qwen2.5:0.5b",
  adapter: "ollama",
  metric: "capability_profile",
  total_samples: 10,
  passed_samples: 8,
  average_score: 0.8,
  failed_sample_ids: [],
  failed_examples: [],
  capability_profile: profile,
};

it("renders a model capability hexagon for a partial suite without inventing missing scores", () => {
  render(<EvaluationResultDetail result={result} />);

  expect(screen.getByText("LLM 六维能力画像")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "六维模型能力雷达图" })).toBeInTheDocument();
  const radar = screen.getByRole("img", { name: "六维模型能力雷达图" });
  expect(radar.querySelectorAll("polygon")).toHaveLength(4);
  expect(radar.querySelectorAll("circle")).toHaveLength(2);
  expect(screen.getByText("2 / 6 已评测")).toBeInTheDocument();
  expect(screen.getByText("部分完成")).toBeInTheDocument();
  expect(screen.getAllByText("—")).toHaveLength(4);
});

it("derives profile completeness from six assessed axes for legacy results", () => {
  render(
    <EvaluationResultDetail
      result={{
        ...result,
        capability_profile: { ...profile, status: "complete" },
      }}
    />,
  );

  expect(screen.getByText("2 / 6 已评测")).toBeInTheDocument();
  expect(screen.queryByText("完整画像")).toBeNull();
});

it("shows frozen suite reproducibility as a compact disclosure", () => {
  render(
    <EvaluationResultDetail
      result={{
        ...result,
        reproducibility: {
          suite_version: "1.0.0",
          manifest_sha256: "sha256:manifest",
          source_revisions: { "hexagon-gsm8k": "revision-1" },
          prompt_template_versions: { "hexagon-gsm8k": "evalhub-v1" },
          generation_config: { temperature: 0, num_predict: 256 },
        },
      }}
    />,
  );

  expect(screen.getByText("可复现性账本")).toBeInTheDocument();
  expect(screen.getByText("sha256:manifest")).toBeInTheDocument();
  expect(screen.getByText("hexagon-gsm8k: revision-1")).toBeInTheDocument();
});
