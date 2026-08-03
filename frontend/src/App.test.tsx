import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  cancelModelPull,
  getDatasets,
  getHealth,
  getModelPull,
  getOllamaStatus,
  prepareDataset,
  runEvaluation,
  startModelPull,
} from "./lib/api";

vi.mock("./lib/api", () => ({
  cancelModelPull: vi.fn(),
  getDatasets: vi.fn(),
  getHealth: vi.fn(),
  getModelPull: vi.fn(),
  getOllamaStatus: vi.fn(),
  prepareDataset: vi.fn(),
  runEvaluation: vi.fn(),
  startModelPull: vi.fn(),
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
      size_bytes: 397_000_000,
      size_kind: "actual" as const,
    },
    {
      name: "qwen2.5:1.5b",
      label: "Qwen2.5 1.5B",
      description: "轻量中文能力更好",
      installed: false,
      size_bytes: 986_000_000,
      size_kind: "estimated" as const,
    },
  ],
  message: "Ollama 已就绪。",
};

const pullingTask = {
  model: "qwen2.5:1.5b",
  status: "pulling" as const,
  message: "pulling layer",
  completed_bytes: 500_000_000,
  total_bytes: 1_000_000_000,
  speed_bytes_per_second: 25_000_000,
  eta_seconds: 20,
  error: null,
};

const evaluationFixture = {
  job_id: "job_1",
  status: "success",
  dataset: "gsm8k" as const,
  benchmark: "GSM8K 测试集",
  model: "qwen2.5:0.5b",
  adapter: "oracle" as const,
  metric: "numeric_exact_match",
  total_samples: 5,
  passed_samples: 4,
  average_score: 0.8,
  failed_sample_ids: ["sample_5"],
  failed_examples: [
    {
      sample_id: "sample_5",
      score: 0,
      input: "1 + 1",
      prediction: "3",
      reference: "2",
      reason: "数值不匹配",
    },
  ],
};

function navigationButton(name: "概览" | "发起评测" | "资产管理" | "评测结果") {
  return within(screen.getByRole("navigation", { name: "工作区目录" })).getByRole("button", {
    name: `打开${name}页面`,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getHealth).mockResolvedValue({ status: "ok", service: "evalhub" });
  vi.mocked(getDatasets).mockResolvedValue({ datasets: [datasetFixture, mmluFixture] });
  vi.mocked(getOllamaStatus).mockImplementation(async (model, baseUrl) => {
    const present = ollamaFixture.models.includes(model);
    return {
      ...ollamaFixture,
      model,
      base_url: baseUrl,
      model_present: present,
      message: present ? "Ollama 已就绪。" : `Ollama 正在运行，但未找到模型 ${model}。`,
    };
  });
  vi.mocked(getModelPull).mockResolvedValue({ ok: true, task: null });
  vi.mocked(startModelPull).mockResolvedValue({ ok: true, task: pullingTask });
  vi.mocked(cancelModelPull).mockResolvedValue({
    ok: true,
    task: { ...pullingTask, status: "canceled", message: "下载已取消" },
  });
  vi.mocked(prepareDataset).mockResolvedValue({
    ok: true,
    dataset: "gsm8k",
    path: datasetFixture.local_path,
    operation: "cached",
    sample_count: 1319,
  });
  vi.mocked(runEvaluation).mockReset();
});

describe("EvalHub console", () => {
  it("opens on a focused overview with four real workspace destinations", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "工作台概览" })).toBeInTheDocument();
    expect(screen.getByText("本地环境")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "工作区目录" });
    expect(within(navigation).getByRole("button", { name: "打开概览页面" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByRole("button", { name: "打开发起评测页面" })).toBeInTheDocument();
    expect(within(navigation).getByRole("button", { name: "打开资产管理页面" })).toBeInTheDocument();
    expect(within(navigation).getByRole("button", { name: "打开评测结果页面" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "评测就绪轨道" })).toBeVisible();
    expect(screen.getByRole("region", { name: "本地推理环境", hidden: true })).not.toBeVisible();
  });

  it("switches between focused sidebar views", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
    expect(screen.getByRole("heading", { level: 1, name: "发起评测" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建评测" })).toBeVisible();

    await user.click(navigationButton("资产管理"));
    expect(screen.getByRole("heading", { level: 1, name: "资产管理" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "本地推理环境" })).toBeVisible();
    expect(screen.getByRole("region", { name: "数据集资产" })).toBeVisible();

    await user.click(navigationButton("评测结果"));
    expect(screen.getByRole("heading", { level: 1, name: "评测结果" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "评测结果" })).toBeVisible();
  });

  it("keeps evaluation form choices while moving between views", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await user.selectOptions(await screen.findByLabelText("数据集"), "mmlu");
    await user.click(screen.getByRole("radio", { name: "快速试跑" }));
    await user.click(navigationButton("资产管理"));
    await user.click(navigationButton("发起评测"));

    expect(screen.getByLabelText("数据集")).toHaveValue("mmlu");
    expect(screen.getByRole("radio", { name: "快速试跑" })).toBeChecked();
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

    await user.click(navigationButton("发起评测"));

    const datasetSelect = await screen.findByLabelText("数据集");
    expect(screen.queryByLabelText("MMLU 学科")).not.toBeInTheDocument();

    await user.selectOptions(datasetSelect, "mmlu");
    expect(screen.getByLabelText("MMLU 学科")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "自定义" }));
    expect(screen.getByLabelText("自定义样本数量")).toBeInTheDocument();
  });

  it("offers an explicit download choice with size and transparent time estimate", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
    const modelSelect = await screen.findByLabelText("模型");
    await user.selectOptions(modelSelect, "qwen2.5:1.5b");
    await user.click(screen.getByRole("button", { name: "前往资产管理" }));

    expect(await screen.findByText("约 986 MB")).toBeInTheDocument();
    expect(screen.getByText(/按 20–100 Mbps/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下载 qwen2.5:1.5b" }));
    expect(startModelPull).toHaveBeenCalledWith(
      "qwen2.5:1.5b",
      "http://127.0.0.1:11434",
    );
  });

  it("declines a download without a network call and returns to an installed model", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
    const modelSelect = await screen.findByLabelText("模型");
    await user.selectOptions(modelSelect, "qwen2.5:1.5b");
    await user.click(screen.getByRole("button", { name: "前往资产管理" }));
    await user.click(
      await screen.findByRole("button", { name: "暂不下载 qwen2.5:1.5b" }),
    );

    expect(startModelPull).not.toHaveBeenCalled();
    await user.click(navigationButton("发起评测"));
    expect(modelSelect).toHaveValue("qwen2.5:0.5b");
  });

  it("shows real pull telemetry and allows cancellation", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await user.selectOptions(await screen.findByLabelText("模型"), "qwen2.5:1.5b");
    await user.click(screen.getByRole("button", { name: "前往资产管理" }));
    await user.click(
      await screen.findByRole("button", { name: "下载 qwen2.5:1.5b" }),
    );

    expect(await screen.findByRole("progressbar", { name: "qwen2.5:1.5b 下载进度" })).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
    expect(screen.getByText("500 MB / 1.0 GB")).toBeInTheDocument();
    expect(screen.getByText("25 MB/s")).toBeInTheDocument();
    expect(screen.getByText("预计剩余 20 秒")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消 qwen2.5:1.5b 下载" }));
    expect(cancelModelPull).toHaveBeenCalledWith("qwen2.5:1.5b");
  });

  it("blocks Ollama evaluation for a missing model while keeping Oracle available", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await user.selectOptions(await screen.findByLabelText("模型"), "qwen2.5:1.5b");
    expect(await screen.findByText("先下载模型或选择已安装模型")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发起评测" })).toBeDisabled();
    expect(runEvaluation).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText("模型适配器"), "oracle");
    expect(screen.getByRole("button", { name: "发起评测" })).toBeEnabled();
  });

  it("blocks an invalid custom sample count before calling the API", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("发起评测"));
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
      operation: "cached",
      sample_count: 100,
    });
    render(<App />);

    await user.click(navigationButton("资产管理"));
    expect(await screen.findByText("已缓存")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看 GSM8K 测试集数据来源" })).toHaveAttribute(
      "href",
      datasetFixture.homepage,
    );

    await user.click(screen.getByRole("button", { name: "缓存 MMLU 测试集" }));
    expect(prepareDataset).toHaveBeenCalledWith("mmlu", false);
  });

  it("starts with a directed evaluation empty state", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("评测结果"));
    const resultPanel = await screen.findByRole("region", { name: "评测结果" });
    expect(within(resultPanel).getByText("尚未运行评测")).toBeInTheDocument();
    expect(within(resultPanel).getByText("配置上方参数后发起第一次评测。")).toBeInTheDocument();
  });

  it("presents a successful evaluation before collapsed raw JSON", async () => {
    const user = userEvent.setup();
    vi.mocked(runEvaluation).mockResolvedValue(evaluationFixture);
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await screen.findByLabelText("数据集");
    await user.click(screen.getByRole("button", { name: "发起评测" }));

    const resultPanel = await screen.findByRole("region", { name: "评测结果" });
    expect(await within(resultPanel).findByText("0.8000")).toBeInTheDocument();
    expect(within(resultPanel).getByText("4 / 5")).toBeInTheDocument();
    expect(within(resultPanel).getByText("80%")).toBeInTheDocument();
    const details = within(resultPanel).getByText("原始 JSON").closest("details");
    expect(details).not.toHaveAttribute("open");
  });

  it("keeps dataset content available when only Ollama status fails", async () => {
    const user = userEvent.setup();
    vi.mocked(getOllamaStatus).mockRejectedValue(new Error("无法连接 Ollama"));
    render(<App />);

    await user.click(navigationButton("资产管理"));
    expect(await screen.findByText("无法连接 Ollama")).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /GSM8K 测试集/ })).toBeInTheDocument();
  });

  it("shows evaluation failures inside the result module", async () => {
    const user = userEvent.setup();
    vi.mocked(runEvaluation).mockRejectedValue(new Error("评测执行失败：模型不可用"));
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await screen.findByLabelText("数据集");
    await user.click(screen.getByRole("button", { name: "发起评测" }));

    const resultPanel = await screen.findByRole("region", { name: "评测结果" });
    expect(await within(resultPanel).findByRole("alert")).toHaveTextContent("评测执行失败：模型不可用");
  });
});
