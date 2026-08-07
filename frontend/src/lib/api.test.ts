import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createModelProvider,
  deleteModelProvider,
  getAgents,
  getBenchmarks,
  getEvaluationNode,
  getEvaluationNodeSamples,
  getHealth,
  getModelPerformance,
  getModelProviders,
  getOllamaStatus,
  getSuites,
  retryEvaluationNode,
  runEvaluation,
  testModelProvider,
  updateModelProvider,
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

  it("encodes the selected model performance scope", async () => {
    const response = {
      scopes: [],
      selected_scope: null,
      models: [],
      record: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getModelPerformance("benchmark:gsm8k")).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model-performance?scope=benchmark%3Agsm8k",
      expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
    );
  });

  it("requests Agent performance as a separate score type", async () => {
    const response = {
      scopes: [],
      selected_scope: null,
      models: [],
      record: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getModelPerformance("benchmark:coding_mini", "agent")).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model-performance?scope=benchmark%3Acoding_mini&evaluation_type=agent",
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

  it("uses stable registry and node audit endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ agents: [] }))
      .mockResolvedValueOnce(jsonResponse({ benchmarks: [] }))
      .mockResolvedValueOnce(jsonResponse({ suites: [] }))
      .mockResolvedValueOnce(jsonResponse({ node: { id: "node/1" } }))
      .mockResolvedValueOnce(jsonResponse({ samples: [], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, node: { id: "node/1" } }, 202));
    vi.stubGlobal("fetch", fetchMock);

    await getAgents();
    await getBenchmarks();
    await getSuites();
    await getEvaluationNode("job 1", "node/1");
    await getEvaluationNodeSamples("job 1", "node/1", {
      status: "failed",
      limit: 20,
      cursor: "4:sample 5",
    });
    await retryEvaluationNode("job 1", "node/1");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/agents",
      "/api/benchmarks",
      "/api/suites",
      "/api/evaluations/job%201/nodes/node%2F1",
      "/api/evaluations/job%201/nodes/node%2F1/samples?status=failed&limit=20&cursor=4%3Asample+5",
      "/api/evaluations/job%201/nodes/node%2F1/retry",
    ]);
    expect(fetchMock.mock.calls[5]?.[1]).toEqual(
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("uses stable provider CRUD and model discovery endpoints", async () => {
    const provider = {
      id: "deepseek",
      name: "DeepSeek",
      kind: "builtin",
      base_url: "https://api.deepseek.com",
      key_configured: true,
      key_hint: "1234",
      created_at: null,
      updated_at: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ providers: [provider] }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, provider }, 201))
      .mockResolvedValueOnce(jsonResponse({ ok: true, provider }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, models: ["deepseek-v4-pro"] }))
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, provider_id: "provider/custom", reset: false }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await getModelProviders();
    await createModelProvider({
      name: "Gateway",
      base_url: "https://gateway.example.com/v1",
      api_key: "sk-secret",
    });
    await updateModelProvider("provider/custom", { api_key: "" });
    await testModelProvider("provider/custom");
    await deleteModelProvider("provider/custom");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/model-providers",
      "/api/model-providers",
      "/api/model-providers/provider%2Fcustom",
      "/api/model-providers/provider%2Fcustom/test",
      "/api/model-providers/provider%2Fcustom",
    ]);
    expect(fetchMock.mock.calls.map(([, options]) => options?.method)).toEqual([
      undefined,
      "POST",
      "PUT",
      "POST",
      "DELETE",
    ]);
  });
});
