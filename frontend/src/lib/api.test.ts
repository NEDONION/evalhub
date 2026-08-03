import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  cancelModelPull,
  getHealth,
  getModelPull,
  getOllamaStatus,
  prepareDataset,
  runEvaluation,
  startModelPull,
} from "./api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ollamaFixture = {
  installed: true,
  running: true,
  model_present: true,
  command: "/usr/local/bin/ollama",
  base_url: "http://127.0.0.1:11434",
  model: "qwen 2.5:0.5b",
  models: ["qwen 2.5:0.5b"],
  model_options: [],
  message: "Ollama 已就绪。",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EvalHub API", () => {
  it("encodes Ollama status query parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(ollamaFixture));
    vi.stubGlobal("fetch", fetchMock);

    await getOllamaStatus("qwen 2.5:0.5b", "http://127.0.0.1:11434");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ollama/status?model=qwen+2.5%3A0.5b&base_url=http%3A%2F%2F127.0.0.1%3A11434",
      expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
    );
  });

  it("converts unsuccessful JSON responses to ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ok: false, error: "Ollama 不可用" }, 500)));

    await expect(getHealth()).rejects.toEqual(new ApiError("Ollama 不可用", 500));
  });

  it("posts an evaluation request as JSON", async () => {
    const result = {
      job_id: "job_1",
      status: "success",
      dataset: "gsm8k",
      benchmark: "GSM8K",
      model: "qwen2.5:0.5b",
      adapter: "oracle",
      metric: "numeric_exact_match",
      total_samples: 5,
      passed_samples: 5,
      average_score: 1,
      failed_sample_ids: [],
      failed_examples: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, result }));
    vi.stubGlobal("fetch", fetchMock);
    const request = {
      dataset: "gsm8k" as const,
      adapter: "oracle" as const,
      model: "qwen2.5:0.5b",
      base_url: "http://127.0.0.1:11434",
      sample_mode: "quick" as const,
    };

    await expect(runEvaluation(request)).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evaluations/run",
      expect.objectContaining({ method: "POST", body: JSON.stringify(request) }),
    );
  });

  it("creates a streaming Ollama pull task with an explicit model choice", async () => {
    const task = {
      model: "qwen2.5:1.5b",
      status: "pulling",
      message: "pulling layer",
      completed_bytes: 50,
      total_bytes: 100,
      speed_bytes_per_second: 25,
      eta_seconds: 2,
      error: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, task }, 202));
    vi.stubGlobal("fetch", fetchMock);

    await expect(startModelPull("qwen2.5:1.5b", "http://127.0.0.1:11434")).resolves.toEqual({
      ok: true,
      task,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ollama/pulls",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          model: "qwen2.5:1.5b",
          base_url: "http://127.0.0.1:11434",
        }),
      }),
    );
  });

  it("encodes model names when querying and canceling pull tasks", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse({ ok: true, task: null })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getModelPull("qwen2.5:1.5b");
    await cancelModelPull("qwen2.5:1.5b");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/ollama/pulls?model=qwen2.5%3A1.5b",
      expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/ollama/pulls?model=qwen2.5%3A1.5b",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("sends an explicit force flag when updating a cached dataset", async () => {
    const response = {
      ok: true,
      dataset: "gsm8k",
      path: "data/raw/gsm8k/test.jsonl",
      operation: "updated",
      sample_count: 1319,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(prepareDataset("gsm8k", true)).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/datasets/prepare",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ dataset: "gsm8k", force: true }),
      }),
    );
  });
});
