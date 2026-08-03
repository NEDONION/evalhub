import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelEvaluationTask,
  cancelModelPull,
  createEvaluation,
  getBenchmarks,
  getDatasets,
  getEvaluationTask,
  getEvaluationTasks,
  getHealth,
  getModelPerformance,
  getModelPull,
  getOllamaStatus,
  getSuites,
  prepareDataset,
  retryEvaluationNode,
  startModelPull,
} from "../lib/api";
import type {
  EvaluationTaskDetail,
  EvaluationTaskSummary,
  ModelPerformanceResponse,
  OllamaPullTask,
  OllamaStatus,
} from "../types";
import { useEvalHub } from "./useEvalHub";

vi.mock("../lib/api", () => ({
  cancelEvaluationTask: vi.fn(),
  cancelModelPull: vi.fn(),
  createEvaluation: vi.fn(),
  getBenchmarks: vi.fn(),
  getDatasets: vi.fn(),
  getEvaluationTask: vi.fn(),
  getEvaluationTasks: vi.fn(),
  getHealth: vi.fn(),
  getModelPerformance: vi.fn(),
  getModelPull: vi.fn(),
  getOllamaStatus: vi.fn(),
  getSuites: vi.fn(),
  prepareDataset: vi.fn(),
  retryEvaluationNode: vi.fn(),
  startModelPull: vi.fn(),
}));

const ollamaStatus: OllamaStatus = {
  installed: true,
  running: true,
  model_present: false,
  command: "/usr/local/bin/ollama",
  base_url: "http://127.0.0.1:11434",
  model: "qwen2.5:1.5b",
  models: ["qwen2.5:0.5b"],
  model_options: [
    {
      name: "qwen2.5:1.5b",
      label: "Qwen2.5 1.5B",
      description: "轻量中文模型",
      installed: false,
      size_bytes: 986_000_000,
      size_kind: "estimated",
    },
  ],
  message: "缺少模型",
};

const pullingTask: OllamaPullTask = {
  model: "qwen2.5:1.5b",
  status: "pulling",
  message: "pulling layer",
  completed_bytes: 500,
  total_bytes: 1000,
  speed_bytes_per_second: 250,
  eta_seconds: 2,
  error: null,
};

const successTask: OllamaPullTask = {
  ...pullingTask,
  status: "success",
  message: "模型下载完成",
  completed_bytes: 1000,
  eta_seconds: 0,
};

const runningEvaluationTask: EvaluationTaskSummary = {
  id: "task-running",
  status: "running",
  dataset: "gsm8k",
  model: "local-test",
  adapter: "oracle",
  progress: { completed_samples: 1, total_samples: 5, percent: 20 },
  timing: {
    created_at: "2026-08-04T02:00:00+00:00",
    started_at: "2026-08-04T02:00:01+00:00",
    finished_at: null,
    elapsed_seconds: 12,
  },
  resources: {
    cpu: { current_percent: 10, peak_percent: 20 },
    memory: { current_bytes: 1024, peak_bytes: 2048 },
    gpu: {
      supported: false,
      current_percent: null,
      peak_percent: null,
      current_memory_bytes: null,
      peak_memory_bytes: null,
    },
  },
  result_summary: null,
  error_message: null,
};

const runningEvaluationDetail: EvaluationTaskDetail = {
  ...runningEvaluationTask,
  request: {
    dataset: "gsm8k",
    adapter: "oracle",
    model: "local-test",
    base_url: "http://127.0.0.1:11434",
    sample_mode: "quick",
  },
  result: null,
};

const emptyPerformance: ModelPerformanceResponse = {
  scopes: [],
  selected_scope: null,
  models: [],
  record: null,
};

/** 构造只有选中范围的轻量成绩响应，专门用于请求顺序测试。 */
function scopedPerformance(id: string, label: string): ModelPerformanceResponse {
  const scope = {
    key: `benchmark:${id}`,
    kind: "benchmark" as const,
    id,
    label,
    run_count: 1,
  };
  return { scopes: [scope], selected_scope: scope, models: [], record: null };
}

/**
 * 创建可由测试显式完成的 Promise，用于稳定控制并发响应顺序。
 *
 * @returns Promise、成功解析函数和失败函数组成的测试控制器。
 */
function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getHealth).mockResolvedValue({ status: "ok", service: "evalhub" });
  vi.mocked(getModelPerformance).mockResolvedValue(emptyPerformance);
  vi.mocked(getDatasets).mockResolvedValue({ datasets: [] });
  vi.mocked(getBenchmarks).mockResolvedValue({ benchmarks: [] });
  vi.mocked(getSuites).mockResolvedValue({ suites: [] });
  vi.mocked(getOllamaStatus).mockResolvedValue(ollamaStatus);
  vi.mocked(getModelPull).mockResolvedValue({ ok: true, task: null });
  vi.mocked(startModelPull).mockResolvedValue({ ok: true, task: pullingTask });
  vi.mocked(cancelModelPull).mockResolvedValue({
    ok: true,
    task: { ...pullingTask, status: "canceled", message: "下载已取消" },
  });
  vi.mocked(prepareDataset).mockResolvedValue({
    ok: true,
    dataset: "gsm8k",
    path: "data/raw/gsm8k/test.jsonl",
    operation: "updated",
    sample_count: 1319,
  });
  vi.mocked(getEvaluationTasks).mockResolvedValue([]);
  vi.mocked(getEvaluationTask).mockReset();
  vi.mocked(createEvaluation).mockReset();
  vi.mocked(cancelEvaluationTask).mockReset();
  vi.mocked(retryEvaluationNode).mockReset();
});

describe("useEvalHub local asset orchestration", () => {
  it("loads model performance and switches the comparison scope", async () => {
    const scopedPerformance: ModelPerformanceResponse = {
      ...emptyPerformance,
      selected_scope: {
        key: "benchmark:gsm8k",
        kind: "benchmark",
        id: "gsm8k",
        label: "GSM8K",
        run_count: 1,
      },
    };
    vi.mocked(getModelPerformance)
      .mockResolvedValueOnce(emptyPerformance)
      .mockResolvedValueOnce(scopedPerformance);
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );

    await waitFor(() => expect(result.current.modelPerformance).toEqual(emptyPerformance));
    await act(async () => {
      await result.current.selectPerformanceScope("benchmark:gsm8k");
    });

    expect(getModelPerformance).toHaveBeenLastCalledWith("benchmark:gsm8k");
    expect(result.current.modelPerformance).toEqual(scopedPerformance);
  });

  it("loads the initial performance snapshot after the task list snapshot", async () => {
    const tasksRequest = deferred<EvaluationTaskSummary[]>();
    vi.mocked(getEvaluationTasks).mockReturnValue(tasksRequest.promise);
    renderHook(() => useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"));

    await waitFor(() => expect(getHealth).toHaveBeenCalledTimes(1));
    expect(getModelPerformance).not.toHaveBeenCalled();

    await act(async () => {
      tasksRequest.resolve([]);
      await tasksRequest.promise;
    });

    await waitFor(() => expect(getModelPerformance).toHaveBeenCalledWith(undefined));
  });

  it("retries performance loading when a newly scored snapshot hits a transient error", async () => {
    const latestPerformance = scopedPerformance("gsm8k", "GSM8K");
    const scoredTask = {
      ...runningEvaluationTask,
      status: "success" as const,
      evaluation_type: "model" as const,
      result_summary: {
        benchmark: "GSM8K",
        total_samples: 5,
        passed_samples: 4,
        average_score: 0.8,
      },
    };
    vi.mocked(getEvaluationTasks).mockResolvedValue([scoredTask]);
    vi.mocked(getModelPerformance)
      .mockRejectedValueOnce(new Error("数据库暂时忙"))
      .mockResolvedValue(latestPerformance);
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );

    await waitFor(() => expect(getModelPerformance).toHaveBeenCalledTimes(2));

    expect(result.current.modelPerformance).toEqual(latestPerformance);
    expect(result.current.modelPerformanceError).toBeNull();
  });

  it("keeps a requested scope when a header refresh overlaps its response", async () => {
    const initialPerformance = scopedPerformance("gsm8k", "GSM8K");
    const requestedPerformance = scopedPerformance("mmlu", "MMLU");
    const scopeRequest = deferred<ModelPerformanceResponse>();
    vi.mocked(getModelPerformance)
      .mockResolvedValueOnce(initialPerformance)
      .mockReturnValueOnce(scopeRequest.promise)
      .mockResolvedValueOnce(requestedPerformance);
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );
    await waitFor(() => expect(result.current.modelPerformance).toEqual(initialPerformance));

    let scopeLoad!: Promise<void>;
    act(() => {
      scopeLoad = result.current.selectPerformanceScope("benchmark:mmlu");
    });
    await waitFor(() => expect(getModelPerformance).toHaveBeenCalledTimes(2));
    await act(async () => {
      await result.current.refresh();
      scopeRequest.resolve(requestedPerformance);
      await scopeLoad;
    });

    expect(getModelPerformance).toHaveBeenLastCalledWith("benchmark:mmlu");
    expect(result.current.modelPerformance).toEqual(requestedPerformance);
  });

  it("retains the server-selected default scope for later score refreshes", async () => {
    const initialPerformance = scopedPerformance("gsm8k", "GSM8K");
    vi.mocked(getModelPerformance).mockResolvedValue(initialPerformance);
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );
    await waitFor(() => expect(result.current.modelPerformance).toEqual(initialPerformance));

    await act(async () => {
      await result.current.refresh();
    });

    expect(getModelPerformance).toHaveBeenLastCalledWith("benchmark:gsm8k");
  });

  it("keeps the latest model performance scope when requests finish out of order", async () => {
    const firstRequest = deferred<ModelPerformanceResponse>();
    const secondRequest = deferred<ModelPerformanceResponse>();
    const firstScope = {
      ...emptyPerformance,
      selected_scope: {
        key: "benchmark:gsm8k",
        kind: "benchmark" as const,
        id: "gsm8k",
        label: "GSM8K",
        run_count: 1,
      },
    };
    const secondScope = {
      ...emptyPerformance,
      selected_scope: {
        key: "benchmark:mmlu",
        kind: "benchmark" as const,
        id: "mmlu",
        label: "MMLU",
        run_count: 1,
      },
    };
    vi.mocked(getModelPerformance)
      .mockResolvedValueOnce(emptyPerformance)
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise);
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );
    await waitFor(() => expect(result.current.modelPerformance).toEqual(emptyPerformance));

    let firstLoad!: Promise<void>;
    let secondLoad!: Promise<void>;
    act(() => {
      firstLoad = result.current.selectPerformanceScope("benchmark:gsm8k");
      secondLoad = result.current.selectPerformanceScope("benchmark:mmlu");
    });
    await act(async () => {
      secondRequest.resolve(secondScope);
      await secondLoad;
      firstRequest.resolve(firstScope);
      await firstLoad;
    });

    expect(result.current.modelPerformance).toEqual(secondScope);
  });

  it("recovers an active pull task when the selected model loads", async () => {
    vi.mocked(getModelPull).mockResolvedValue({ ok: true, task: pullingTask });

    const { result, unmount } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );

    await waitFor(() => expect(result.current.modelPullTask).toEqual(pullingTask));
    expect(getModelPull).toHaveBeenCalledWith("qwen2.5:1.5b");
    unmount();
  });

  it("polls an explicitly started pull and refreshes readiness after success", async () => {
    vi.mocked(getModelPull)
      .mockResolvedValueOnce({ ok: true, task: null })
      .mockResolvedValue({ ok: true, task: successTask });
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );
    await waitFor(() => expect(getOllamaStatus).toHaveBeenCalledTimes(1));

    await act(async () => {
      await result.current.startModelPull("qwen2.5:1.5b");
    });

    await waitFor(() => expect(result.current.modelPullTask?.status).toBe("success"), {
      timeout: 1500,
    });
    expect(startModelPull).toHaveBeenCalledWith(
      "qwen2.5:1.5b",
      "http://127.0.0.1:11434",
    );
    await waitFor(() => expect(getOllamaStatus).toHaveBeenCalledTimes(2));
  });

  it("cancels the active model pull and keeps the terminal task visible", async () => {
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );
    await act(async () => {
      await result.current.startModelPull("qwen2.5:1.5b");
      await result.current.cancelModelPull("qwen2.5:1.5b");
    });

    expect(cancelModelPull).toHaveBeenCalledWith("qwen2.5:1.5b");
    expect(result.current.modelPullTask?.status).toBe("canceled");
  });

  it("turns a pull creation failure into visible state instead of an unhandled rejection", async () => {
    vi.mocked(startModelPull).mockRejectedValue(new Error("Ollama 服务不可用"));
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );

    await act(async () => {
      await expect(result.current.startModelPull("qwen2.5:1.5b")).resolves.toBeNull();
    });

    expect(result.current.modelPullError).toBe("Ollama 服务不可用");
  });

  it("passes force and exposes a dataset update success notice", async () => {
    const { result } = renderHook(() =>
      useEvalHub("qwen2.5:1.5b", "http://127.0.0.1:11434"),
    );

    await act(async () => {
      await result.current.prepare("gsm8k", true);
    });

    expect(prepareDataset).toHaveBeenCalledWith("gsm8k", true);
    expect(result.current.datasetNotice).toBe("GSM8K 已更新，1,319 条样本");
  });
});

describe("useEvalHub evaluation task orchestration", () => {
  it("retries a failed node and refreshes the selected task detail", async () => {
    const failedDetail: EvaluationTaskDetail = {
      ...runningEvaluationDetail,
      status: "failed",
    };
    vi.mocked(getEvaluationTasks).mockResolvedValue([{ ...runningEvaluationTask, status: "failed" }]);
    vi.mocked(getEvaluationTask).mockResolvedValue(failedDetail);
    vi.mocked(retryEvaluationNode).mockResolvedValue({
      id: "node-1",
      task_id: failedDetail.id,
      node_key: "benchmark:gsm8k",
      kind: "benchmark",
      depends_on: ["prepare_assets"],
      status: "pending",
      attempt: { count: 0, max: 3 },
      progress: { completed_samples: 4, total_samples: 5, percent: 80 },
      timing: {
        created_at: failedDetail.timing.created_at,
        started_at: failedDetail.timing.started_at,
        finished_at: null,
        elapsed_ms: 1200,
      },
      error: null,
    });
    const { result } = renderHook(() => useEvalHub("local-test", "http://127.0.0.1:11434"));
    await waitFor(() => expect(result.current.selectedTask?.id).toBe(failedDetail.id));

    await act(async () => {
      await result.current.retryNode(failedDetail.id, "node-1");
    });

    expect(retryEvaluationNode).toHaveBeenCalledWith(failedDetail.id, "node-1");
    expect(getEvaluationTask).toHaveBeenCalledTimes(2);
    expect(result.current.retryingNodeId).toBeNull();
  });

  it("waits for a slow task poll before scheduling the next request", async () => {
    const slowPoll = deferred<EvaluationTaskSummary[]>();
    vi.mocked(getEvaluationTasks)
      .mockResolvedValueOnce([runningEvaluationTask])
      .mockImplementationOnce(() => slowPoll.promise)
      .mockResolvedValue([runningEvaluationTask]);
    vi.mocked(getEvaluationTask).mockResolvedValue(runningEvaluationDetail);
    renderHook(() => useEvalHub("local-test", "http://127.0.0.1:11434"));

    await waitFor(() => expect(getEvaluationTasks).toHaveBeenCalledTimes(2), { timeout: 1800 });
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    expect(getEvaluationTasks).toHaveBeenCalledTimes(2);

    await act(async () => slowPoll.resolve([runningEvaluationTask]));
    await waitFor(() => expect(getEvaluationTasks).toHaveBeenCalledTimes(3), { timeout: 1800 });
  }, 6000);

  it("does not replace a newly selected task when an earlier cancel finishes", async () => {
    const completedTask: EvaluationTaskSummary = {
      ...runningEvaluationTask,
      id: "task-completed",
      status: "success",
      result_summary: {
        benchmark: "GSM8K 测试集",
        total_samples: 5,
        passed_samples: 5,
        average_score: 1,
      },
    };
    const completedDetail: EvaluationTaskDetail = {
      ...runningEvaluationDetail,
      ...completedTask,
    };
    const cancelResponse = deferred<EvaluationTaskDetail>();
    vi.mocked(getEvaluationTasks).mockResolvedValue([runningEvaluationTask, completedTask]);
    vi.mocked(getEvaluationTask).mockImplementation(async (taskId) =>
      taskId === completedTask.id ? completedDetail : runningEvaluationDetail,
    );
    vi.mocked(cancelEvaluationTask).mockImplementation(() => cancelResponse.promise);
    const { result } = renderHook(() => useEvalHub("local-test", "http://127.0.0.1:11434"));
    await waitFor(() => expect(result.current.selectedTask?.id).toBe(runningEvaluationTask.id));

    let cancelPromise: Promise<EvaluationTaskDetail | null>;
    act(() => {
      cancelPromise = result.current.cancelTask(runningEvaluationTask.id);
      result.current.selectTask(completedTask.id);
    });
    await waitFor(() => expect(result.current.selectedTask?.id).toBe(completedTask.id));

    await act(async () => {
      cancelResponse.resolve({ ...runningEvaluationDetail, status: "canceled" });
      await cancelPromise!;
    });
    expect(result.current.selectedTask?.id).toBe(completedTask.id);
  });
});
