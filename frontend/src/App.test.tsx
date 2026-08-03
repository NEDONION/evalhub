import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  getDatasets,
  getHealth,
  getOllamaStatus,
  prepareDataset,
  runEvaluation,
} from "./lib/api";

vi.mock("./lib/api", () => ({
  getDatasets: vi.fn(),
  getHealth: vi.fn(),
  getOllamaStatus: vi.fn(),
  prepareDataset: vi.fn(),
  runEvaluation: vi.fn(),
}));

const datasetFixture = {
  name: "gsm8k" as const,
  display_name: "GSM8K 测试集",
  task_type: "math_reasoning",
  evaluator_type: "numeric_exact_match",
  homepage: "https://github.com/openai/grade-school-math",
  source_url: "https://example.com/gsm8k.jsonl",
  local_path: "data/raw/gsm8k/test.jsonl",
  description: "小学数学推理数据集",
  prepared: true,
  sample_count: 1319,
};

const mmluFixture = {
  name: "mmlu" as const,
  display_name: "MMLU 测试集",
  task_type: "multiple_choice",
  evaluator_type: "choice_letter",
  homepage: "https://github.com/hendrycks/test",
  source_url: "https://example.com/mmlu.tar",
  local_path: "data/raw/mmlu/data/test",
  description: "多学科多选题数据集",
  prepared: false,
  sample_count: null,
};

const ollamaFixture = {
  installed: true,
  running: true,
  model_present: true,
  command: "/usr/local/bin/ollama",
  base_url: "http://127.0.0.1:11434",
  model: "qwen2.5:0.5b",
  models: ["qwen2.5:0.5b"],
  model_options: [
    {
      name: "qwen2.5:0.5b",
      label: "Qwen2.5 0.5B",
      description: "默认轻量模型",
      installed: true,
    },
  ],
  message: "Ollama 已就绪。",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getHealth).mockResolvedValue({ status: "ok", service: "evalhub" });
  vi.mocked(getDatasets).mockResolvedValue({ datasets: [datasetFixture, mmluFixture] });
  vi.mocked(getOllamaStatus).mockResolvedValue(ollamaFixture);
  vi.mocked(prepareDataset).mockResolvedValue({ ok: true, dataset: "gsm8k", path: datasetFixture.local_path });
  vi.mocked(runEvaluation).mockReset();
});

describe("EvalHub console", () => {
  it("shows the real dashboard without placeholder navigation", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "模型评测工作台" })).toBeInTheDocument();
    expect(screen.getByText("本地环境")).toBeInTheDocument();
    expect(await screen.findByText("Ollama 已就绪。", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("模型注册")).not.toBeInTheDocument();
    expect(screen.queryByText("排行榜")).not.toBeInTheDocument();
  });

  it("refreshes every status source from the header action", async () => {
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => {
      expect(getHealth).toHaveBeenCalledTimes(1);
      expect(getDatasets).toHaveBeenCalledTimes(1);
      expect(getOllamaStatus).toHaveBeenCalledTimes(1);
    });

    await user.click(screen.getByRole("button", { name: "刷新状态" }));

    await waitFor(() => {
      expect(getHealth).toHaveBeenCalledTimes(2);
      expect(getDatasets).toHaveBeenCalledTimes(2);
      expect(getOllamaStatus).toHaveBeenCalledTimes(2);
    });
  });

  it("shows contextual fields for MMLU and custom sample runs", async () => {
    const user = userEvent.setup();
    render(<App />);

    const datasetSelect = await screen.findByLabelText("数据集");
    expect(screen.queryByLabelText("MMLU 学科")).not.toBeInTheDocument();

    await user.selectOptions(datasetSelect, "mmlu");
    expect(screen.getByLabelText("MMLU 学科")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "自定义" }));
    expect(screen.getByLabelText("自定义样本数量")).toBeInTheDocument();
  });

  it("blocks an invalid custom sample count before calling the API", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByLabelText("数据集");
    await user.click(screen.getByRole("radio", { name: "自定义" }));
    const limit = screen.getByLabelText("自定义样本数量");
    await user.clear(limit);
    await user.type(limit, "0");
    expect(limit).toHaveValue(0);
    await user.click(screen.getByRole("button", { name: "发起评测" }));

    expect(screen.getByText("样本数量必须是大于 0 的整数")).toBeInTheDocument();
    expect(runEvaluation).not.toHaveBeenCalled();
  });

  it("shows real dataset sources and prepares an uncached dataset", async () => {
    const user = userEvent.setup();
    vi.mocked(prepareDataset).mockResolvedValue({
      ok: true,
      dataset: "mmlu",
      path: mmluFixture.local_path,
    });
    render(<App />);

    expect(await screen.findByText("已缓存")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看 GSM8K 测试集数据来源" })).toHaveAttribute(
      "href",
      datasetFixture.homepage,
    );

    await user.click(screen.getByRole("button", { name: "缓存 MMLU 测试集" }));
    expect(prepareDataset).toHaveBeenCalledWith("mmlu");
  });
});
