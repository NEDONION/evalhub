import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelModelPull,
  getDatasets,
  getHealth,
  getModelPull,
  getOllamaStatus,
  prepareDataset,
  runEvaluation,
  startModelPull,
} from "../lib/api";
import type { OllamaPullTask, OllamaStatus } from "../types";
import { useEvalHub } from "./useEvalHub";

vi.mock("../lib/api", () => ({
  cancelModelPull: vi.fn(),
  getDatasets: vi.fn(),
  getHealth: vi.fn(),
  getModelPull: vi.fn(),
  getOllamaStatus: vi.fn(),
  prepareDataset: vi.fn(),
  runEvaluation: vi.fn(),
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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getHealth).mockResolvedValue({ status: "ok", service: "evalhub" });
  vi.mocked(getDatasets).mockResolvedValue({ datasets: [] });
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
  vi.mocked(runEvaluation).mockReset();
});

describe("useEvalHub local asset orchestration", () => {
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
