import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  cancelEvaluationTask,
  cancelModelPull,
  createEvaluation,
  getBenchmarks,
  getDatasets,
  getEvaluationNode,
  getEvaluationNodeSamples,
  getEvaluationTask,
  getEvaluationTasks,
  getHealth,
  getModelPerformance,
  getModelPull,
  getOllamaStatus,
  getSuites,
  prepareDataset,
  startModelPull,
} from "./lib/api";
import type {
  BenchmarkDefinition,
  Dataset,
  EvaluationNodeDetail,
  EvaluationNodeSummary,
  EvaluationTaskDetail,
  EvaluationTaskSummary,
  ModelPerformanceResponse,
} from "./types";

vi.mock("./lib/api", () => ({
  cancelEvaluationTask: vi.fn(),
  cancelModelPull: vi.fn(),
  createEvaluation: vi.fn(),
  getBenchmarks: vi.fn(),
  getDatasets: vi.fn(),
  getEvaluationNode: vi.fn(),
  getEvaluationNodeSamples: vi.fn(),
  getEvaluationTask: vi.fn(),
  getEvaluationTasks: vi.fn(),
  getHealth: vi.fn(),
  getModelPerformance: vi.fn(),
  getModelPull: vi.fn(),
  getOllamaStatus: vi.fn(),
  getSuites: vi.fn(),
  prepareDataset: vi.fn(),
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
  model: "granite4.1:3b",
  models: ["granite4.1:3b"],
  model_options: [
    {
      name: "granite4.1:3b",
      label: "Granite 4.1 3B",
      description: "默认轻量 Agent 模型",
      installed: true,
      size_bytes: 2_100_000_000,
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

const emptyPerformance: ModelPerformanceResponse = {
  scopes: [],
  selected_scope: null,
  models: [],
  record: null,
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

const taskResources = {
  cpu: { current_percent: 12.5, peak_percent: 40 },
  memory: { current_bytes: 1024, peak_bytes: 2048 },
  gpu: {
    supported: false,
    current_percent: null,
    peak_percent: null,
    current_memory_bytes: null,
    peak_memory_bytes: null,
  },
};

const pendingTask: EvaluationTaskSummary = {
  id: "job_pending",
  status: "pending",
  evaluation_type: "model",
  agent_framework: null,
  dataset: "gsm8k",
  model: "qwen2.5:0.5b",
  adapter: "oracle",
  progress: { completed_samples: 0, total_samples: 0, percent: 0 },
  timing: {
    created_at: "2026-08-04T02:00:00+00:00",
    started_at: null,
    finished_at: null,
    elapsed_seconds: 0,
  },
  resources: taskResources,
  result_summary: null,
  error_message: null,
};

const successfulTask: EvaluationTaskSummary = {
  ...pendingTask,
  id: "job_success",
  status: "success",
  progress: { completed_samples: 5, total_samples: 5, percent: 100 },
  timing: {
    ...pendingTask.timing,
    started_at: "2026-08-04T02:00:01+00:00",
    finished_at: "2026-08-04T02:02:15+00:00",
    elapsed_seconds: 135,
  },
  result_summary: {
    benchmark: "GSM8K 测试集",
    total_samples: 5,
    passed_samples: 4,
    average_score: 0.8,
  },
};

const successfulTaskDetail: EvaluationTaskDetail = {
  ...successfulTask,
  request: {
    evaluation_type: "model",
    dataset: "gsm8k",
    adapter: "oracle",
    model: "qwen2.5:0.5b",
    base_url: "http://127.0.0.1:11434",
    sample_mode: "quick",
  },
  result: evaluationFixture,
};

const agentTask: EvaluationTaskSummary = {
  ...successfulTask,
  id: "job_agent_success",
  evaluation_type: "agent",
  agent_framework: "codex",
  dataset: "coding_mini",
  adapter: "ollama",
  resources: {
    ...taskResources,
    gpu: {
      supported: true,
      current_percent: 84,
      peak_percent: 92,
      current_memory_bytes: 3_379_724_288,
      peak_memory_bytes: 3_500_000_000,
    },
  },
  progress: { completed_samples: 2, total_samples: 2, percent: 100 },
  result_summary: {
    benchmark: "EvalHub Coding Mini",
    total_samples: 2,
    passed_samples: 1,
    average_score: 0.5,
  },
};

const agentNodeSummary: EvaluationNodeSummary = {
  id: "node_agent",
  task_id: "job_agent_success",
  node_key: "agent:coding_mini",
  kind: "agent_benchmark",
  depends_on: [],
  status: "success",
  attempt: { count: 1, max: 1 },
  progress: { completed_samples: 2, total_samples: 2, percent: 100 },
  timing: {
    created_at: "2026-08-04T02:00:00+00:00",
    started_at: "2026-08-04T02:00:01+00:00",
    finished_at: "2026-08-04T02:00:10+00:00",
    elapsed_ms: 9000,
  },
  error: null,
};

const agentNodeDetail: EvaluationNodeDetail = {
  ...agentNodeSummary,
  input: { benchmark_id: "coding_mini" },
  checkpoint: { completed_samples: 2, total_samples: 2 },
  output: { passed_samples: 1, total_samples: 2 },
  events: [],
};

const agentTaskDetail: EvaluationTaskDetail = {
  ...agentTask,
  request: {
    evaluation_type: "agent",
    agent_framework: "codex",
    dataset: "coding_mini",
    adapter: "ollama",
    model: "qwen2.5:0.5b",
    base_url: "http://127.0.0.1:11434",
    sample_mode: "all",
    agent_difficulty: "hard",
  },
  nodes: [agentNodeSummary],
  result: {
    job_id: "job_agent_success",
    status: "success",
    evaluation_type: "agent",
    dataset: "coding_mini",
    benchmark: "EvalHub Coding Mini",
    model: "qwen2.5:0.5b",
    adapter: "ollama",
    metric: "hidden_verifier_pass_rate",
    total_samples: 2,
    passed_samples: 1,
    average_score: 0.5,
    failed_sample_ids: ["batch_reservation_atomicity"],
    failed_examples: [
      {
        sample_id: "batch_reservation_atomicity",
        difficulty: "hard",
        difficulty_reason: "需要理解两文件调用关系和原子性",
        score: 0,
        input: "batch_reservation_atomicity",
        prediction: "",
        reference: "hidden verifier passed",
        reason: "hidden verifier failed",
      },
    ],
    benchmark_version: "coding-mini-v2",
    requested_difficulty: "hard",
    difficulty_report: [{ difficulty: "hard", total: 2, passed: 1, pass_rate: 0.5 }],
    agent: {
      framework: "codex",
      cli_version: "codex-cli 0.test",
      scaffold_hash: "a1b2c3d4e5f6",
    },
    capability_report: {
      overall_score: 0.6616,
      dimensions: [
        { key: "planning", label: "规划", score: 0 },
        { key: "code_understanding", label: "代码理解", score: 0.3333 },
        { key: "implementation", label: "实现正确性", score: 0.6364 },
        { key: "tool_use", label: "工具使用", score: 1 },
        { key: "verification", label: "验证能力", score: 1 },
        { key: "robustness", label: "稳健性", score: 1 },
      ],
    },
    sample_results: [],
  },
};

/**
 * 从工作区目录中按可访问名称取得导航按钮，避免与表单内同名业务操作混淆。
 *
 * @param name 用户可见的目录名称。
 * @returns 当前渲染应用中的对应目录按钮。
 */
function navigationButton(name: "概览" | "发起评测" | "资产管理" | "评测结果" | "模型成绩") {
  return within(screen.getByRole("navigation", { name: "工作区目录" })).getByRole("button", {
    name: `打开${name}页面`,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getHealth).mockResolvedValue({ status: "ok", service: "evalhub" });
  vi.mocked(getModelPerformance).mockResolvedValue(emptyPerformance);
  vi.mocked(getDatasets).mockResolvedValue({ datasets: [datasetFixture, mmluFixture] });
  vi.mocked(getBenchmarks).mockResolvedValue({ benchmarks: [] });
  vi.mocked(getSuites).mockResolvedValue({ suites: [] });
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
  vi.mocked(getEvaluationTasks).mockResolvedValue([]);
  vi.mocked(getEvaluationNode).mockResolvedValue(agentNodeDetail);
  vi.mocked(getEvaluationNodeSamples).mockResolvedValue({ samples: [], next_cursor: null });
  vi.mocked(getEvaluationTask).mockReset();
  vi.mocked(createEvaluation).mockReset();
  vi.mocked(cancelEvaluationTask).mockReset();
});

describe("EvalHub console", () => {
  it("opens on a focused overview with five real workspace destinations", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "工作台概览" })).toBeInTheDocument();
    expect(screen.getByText("本地环境")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "工作区目录" });
    expect(within(navigation).getByRole("button", { name: "打开概览页面" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByRole("button", { name: "打开发起评测页面" })).toBeInTheDocument();
    expect(within(navigation).getByRole("button", { name: "打开资产管理页面" })).toBeInTheDocument();
    expect(within(navigation).getByRole("button", { name: "打开评测结果页面" })).toBeInTheDocument();
    expect(within(navigation).getByRole("button", { name: "打开模型成绩页面" })).toBeInTheDocument();
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
    expect(screen.getByRole("heading", { level: 1, name: "评测任务" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "评测任务" })).toBeVisible();

    await user.click(navigationButton("模型成绩"));
    expect(screen.getByRole("heading", { level: 1, name: "模型成绩" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "模型历史成绩" })).toBeVisible();
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
    expect(screen.getByLabelText("MMLU 学科")).toHaveValue("all");

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
    expect(modelSelect).toHaveValue("granite4.1:3b");
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
    expect(createEvaluation).not.toHaveBeenCalled();

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
    expect(createEvaluation).not.toHaveBeenCalled();
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

  it("shows and enables all thirteen registry benchmarks", async () => {
    const user = userEvent.setup();
    const externalRows: Array<
      [string, string, Dataset["executor"], string, string]
    > = [
      ["mmlu-pro", "MMLU-Pro", "lm_eval", "知识", "exact_match"],
      ["ifeval", "IFEval", "lm_eval", "指令遵循", "prompt_level_strict_acc"],
      ["math-500", "MATH-500", "lm_eval", "数学", "exact_match"],
      ["bbh", "BIG-Bench Hard", "lm_eval", "综合推理", "exact_match"],
      ["arc-challenge", "ARC-Challenge", "lm_eval", "综合推理", "acc_norm"],
      ["musr", "MuSR", "lm_eval", "综合推理", "acc_norm"],
      ["hellaswag", "HellaSwag", "lm_eval", "综合推理", "acc_norm"],
      ["humaneval", "HumanEval", "sandboxed_code", "代码", "pass@1"],
      ["mbpp", "MBPP", "sandboxed_code", "代码", "pass@1"],
      ["truthfulqa", "TruthfulQA", "lm_eval", "安全可信", "acc"],
      ["bbq", "BBQ", "lm_eval", "安全可信", "acc"],
    ];
    const datasets: Dataset[] = [datasetFixture, mmluFixture, ...externalRows.map(
      ([name, displayName, executor, capabilityLabel, metric]) => ({
        name,
        display_name: displayName,
        task_type: capabilityLabel,
        evaluator_type: metric,
        homepage: `https://example.com/${name}`,
        source_url: name,
        local_path: `.runtime/benchmarks/${name}.json`,
        description: `${displayName} 官方公开 Benchmark`,
        executor,
        capability: "reasoning",
        capability_label: capabilityLabel,
        locally_runnable: true,
        readiness_reason: null,
        prepared: false,
        sample_count: null,
      })),
    ];
    const benchmarks: BenchmarkDefinition[] = datasets.map((dataset) => ({
      id: dataset.name,
      version: "1.0.0",
      display_name: dataset.display_name,
      capability: dataset.capability || "reasoning",
      capability_label: dataset.capability_label || "综合推理",
      dataset_source: dataset.source_url,
      dataset_revision: "resolved-at-runtime:sha256",
      homepage: dataset.homepage,
      executor: dataset.executor || "native",
      metric: dataset.evaluator_type,
      locally_runnable: true,
      readiness_reason: null,
    }));
    vi.mocked(getDatasets).mockResolvedValue({ datasets });
    vi.mocked(getBenchmarks).mockResolvedValue({ benchmarks });
    vi.mocked(getSuites).mockResolvedValue({
      suites: [
        {
          id: "llm-industry-core-v1",
          version: "1.0.0",
          display_name: "LLM 行业核心套件 v1",
          benchmark_ids: benchmarks.map((item) => item.id),
          benchmark_count: 13,
          locally_runnable_count: 13,
        },
      ],
    });
    vi.mocked(prepareDataset).mockResolvedValue({
      ok: true,
      dataset: "mmlu-pro",
      path: ".runtime/benchmarks/mmlu-pro.json",
      operation: "cached",
      sample_count: null,
    });
    render(<App />);

    await user.click(navigationButton("资产管理"));
    expect(await screen.findByText("13 DATASETS")).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /MMLU-Pro/ });
    expect(within(row).getByText("lm-eval · 精确匹配")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "缓存 MMLU-Pro" }));
    expect(prepareDataset).toHaveBeenCalledWith("mmlu-pro", false);

    await user.click(navigationButton("发起评测"));
    const option = within(await screen.findByLabelText("数据集")).getByRole("option", {
      name: /MMLU-Pro/,
    });
    expect(option).toBeEnabled();
  });

  it("force-refreshes a cached dataset and reports what changed", async () => {
    const user = userEvent.setup();
    vi.mocked(prepareDataset).mockResolvedValue({
      ok: true,
      dataset: "gsm8k",
      path: datasetFixture.local_path,
      operation: "updated",
      sample_count: 1319,
    });
    render(<App />);

    await user.click(navigationButton("资产管理"));
    await user.click(await screen.findByRole("button", { name: "更新 GSM8K 测试集" }));

    expect(prepareDataset).toHaveBeenCalledWith("gsm8k", true);
    expect(await screen.findByText("GSM8K 已更新，1,319 条样本")).toBeInTheDocument();
  });

  it("starts with a directed evaluation empty state", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(navigationButton("评测结果"));
    const resultPanel = await screen.findByRole("region", { name: "评测任务" });
    expect(within(resultPanel).getByText("尚无评测任务")).toBeInTheDocument();
    expect(within(resultPanel).getByText("提交评测后，可在这里追踪进度和资源占用。")).toBeInTheDocument();
  });

  it("shows how many active FIFO tasks are ahead of a pending task", async () => {
    const user = userEvent.setup();
    const runningTask: EvaluationTaskSummary = {
      ...pendingTask,
      id: "job_running",
      status: "running",
      timing: { ...pendingTask.timing, started_at: "2026-08-04T01:59:00+00:00" },
    };
    vi.mocked(getEvaluationTasks).mockResolvedValue([pendingTask, runningTask]);
    vi.mocked(getEvaluationTask).mockResolvedValue({
      ...pendingTask,
      request: successfulTaskDetail.request,
      result: null,
    });
    render(<App />);

    await user.click(navigationButton("评测结果"));

    expect(await screen.findByText("等待 Worker · 前方 1 个任务")).toBeInTheDocument();
  });

  it("labels suite tasks without presenting the first benchmark as the task name", async () => {
    const user = userEvent.setup();
    const suiteTask = { ...pendingTask, id: "job_suite", suite_id: "llm-industry-core-v1" };
    vi.mocked(getEvaluationTasks).mockResolvedValue([suiteTask]);
    vi.mocked(getEvaluationTask).mockResolvedValue({
      ...suiteTask,
      request: { ...successfulTaskDetail.request, suite_id: "llm-industry-core-v1" },
      result: null,
    });
    render(<App />);

    await user.click(navigationButton("评测结果"));

    expect(await screen.findByText("LLM 行业能力套件")).toBeInTheDocument();
  });

  it("presents a successful evaluation before collapsed raw JSON", async () => {
    const user = userEvent.setup();
    vi.mocked(getEvaluationTasks).mockResolvedValue([successfulTask]);
    vi.mocked(getEvaluationTask).mockResolvedValue(successfulTaskDetail);
    render(<App />);

    await user.click(navigationButton("评测结果"));

    const resultPanel = await screen.findByRole("region", { name: "评测任务" });
    expect(await within(resultPanel).findByText("0.8000")).toBeInTheDocument();
    expect(within(resultPanel).getAllByText("4 / 5").length).toBeGreaterThan(0);
    const details = within(resultPanel).getByText("原始 JSON").closest("details");
    expect(details).not.toHaveAttribute("open");
  });

  it("submits a fixed Codex Agent task and shows its six-dimension report", async () => {
    const user = userEvent.setup();
    vi.mocked(createEvaluation).mockResolvedValue(pendingTask);
    vi.mocked(getEvaluationTask)
      .mockResolvedValueOnce({ ...pendingTask, request: successfulTaskDetail.request, result: null })
      .mockResolvedValue(agentTaskDetail);
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await user.click(await screen.findByRole("radio", { name: "Agent 评测" }));
    expect(screen.getByText("EvalHub Coding Mini")).toBeInTheDocument();
    expect(screen.getByText("6 个三级难度隐藏校验任务")).toBeInTheDocument();
    expect(screen.getByText("Codex CLI")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "困难" }));
    await user.click(screen.getByRole("button", { name: "发起 Agent 评测" }));

    expect(createEvaluation).toHaveBeenCalledWith({
      evaluation_type: "agent",
      agent_framework: "codex",
      dataset: "coding_mini",
      adapter: "ollama",
      model: "granite4.1:3b",
      base_url: "http://127.0.0.1:11434",
      sample_mode: "all",
      agent_difficulty: "hard",
    });

    vi.mocked(getEvaluationTasks).mockResolvedValue([agentTask]);
    vi.mocked(getEvaluationTask).mockResolvedValue(agentTaskDetail);
    await user.click(navigationButton("概览"));
    await user.click(navigationButton("评测结果"));
    expect(await screen.findByRole("heading", { name: "Agent 能力报告" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "难度分层" })).toBeInTheDocument();
    expect(screen.getByText("题集 coding-mini-v2 · 请求 困难")).toBeInTheDocument();
    expect(screen.getByText("困难 · 需要理解两文件调用关系和原子性")).toBeInTheDocument();
    expect(await screen.findByText("Agent 实时过程")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Agent 六维能力图" })).toBeInTheDocument();
    expect(screen.getByText("本机峰值 40% · 含 Ollama")).toBeInTheDocument();
    expect(screen.getByText(/系统级 · 设备内存/)).toBeInTheDocument();
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
    vi.mocked(createEvaluation).mockRejectedValue(new Error("评测执行失败：模型不可用"));
    render(<App />);

    await user.click(navigationButton("发起评测"));
    await screen.findByLabelText("数据集");
    await user.click(screen.getByRole("button", { name: "发起评测" }));

    const resultPanel = await screen.findByRole("region", { name: "评测任务" });
    expect(await within(resultPanel).findByRole("alert")).toHaveTextContent("评测执行失败：模型不可用");
  });
});
