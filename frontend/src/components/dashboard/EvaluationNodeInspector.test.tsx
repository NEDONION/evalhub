import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getEvaluationNode, getEvaluationNodeSamples } from "../../lib/api";
import type { EvaluationNodeDetail, EvaluationNodeSummary } from "../../types";
import { EvaluationNodeInspector } from "./EvaluationNodeInspector";

vi.mock("../../lib/api", () => ({
  getEvaluationNode: vi.fn(),
  getEvaluationNodeSamples: vi.fn(),
}));

function node(id: string, benchmark: string): EvaluationNodeSummary {
  return {
    id,
    task_id: "task-1",
    node_key: `benchmark:${benchmark}`,
    kind: "benchmark",
    depends_on: ["prepare_assets"],
    status: "failed",
    attempt: { count: 1, max: 3 },
    progress: { completed_samples: 1, total_samples: 1, percent: 100 },
    timing: {
      created_at: "2026-08-04T01:00:00Z",
      started_at: "2026-08-04T01:00:01Z",
      finished_at: "2026-08-04T01:00:02Z",
      elapsed_ms: 1000,
    },
    error: { type: "runtime_error", message: `${benchmark} failed` },
  };
}

function detail(summary: EvaluationNodeSummary): EvaluationNodeDetail {
  return { ...summary, input: {}, checkpoint: null, output: null, events: [] };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getEvaluationNodeSamples).mockResolvedValue({ samples: [], next_cursor: null });
});

it("clears the old retry target while a newly selected node is loading", async () => {
  const gsm8k = node("node-gsm8k", "gsm8k");
  const mmlu = node("node-mmlu", "mmlu");
  let resolveMmlu: ((value: EvaluationNodeDetail) => void) | undefined;
  vi.mocked(getEvaluationNode).mockImplementation((_taskId, nodeId) => {
    if (nodeId === gsm8k.id) return Promise.resolve(detail(gsm8k));
    return new Promise((resolve) => {
      resolveMmlu = resolve;
    });
  });

  render(
    <EvaluationNodeInspector
      taskId="task-1"
      taskStatus="failed"
      nodes={[gsm8k, mmlu]}
      retryingNodeId={null}
      onRetry={vi.fn()}
    />,
  );
  expect(await screen.findByRole("button", { name: "重试此节点" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /mmlu/ }));

  await waitFor(() => expect(screen.queryByRole("button", { name: "重试此节点" })).toBeNull());
  resolveMmlu?.(detail(mmlu));
  expect(await screen.findByRole("button", { name: "重试此节点" })).toBeInTheDocument();
});

const runningNode: EvaluationNodeSummary = {
  id: "node-agent",
  task_id: "task-agent",
  node_key: "agent:coding_mini",
  kind: "agent_benchmark",
  depends_on: [],
  status: "running",
  attempt: { count: 1, max: 1 },
  progress: { completed_samples: 0, total_samples: 3, percent: 0 },
  timing: {
    created_at: "2026-08-04T02:00:00+00:00",
    started_at: "2026-08-04T02:00:01+00:00",
    finished_at: null,
    elapsed_ms: 100,
  },
  error: null,
};

const initialDetail: EvaluationNodeDetail = {
  ...runningNode,
  input: { benchmark_id: "coding_mini" },
  checkpoint: { completed_samples: 0, total_samples: 3 },
  output: null,
  events: [
    {
      id: 1,
      event_type: "agent_turn_started",
      from_status: null,
      to_status: null,
      attempt: 1,
      actor: "codex",
      message: "Codex 开始处理任务",
      payload: { source_type: "turn.started" },
      created_at: "2026-08-04T02:00:01+00:00",
    },
    {
      id: 2,
      event_type: "sample_started",
      from_status: null,
      to_status: null,
      attempt: 1,
      actor: "benchmark",
      message: "Fix pricing.total_with_tax",
      payload: { sample_id: "pricing_total" },
      created_at: "2026-08-04T02:00:02+00:00",
    },
  ],
};

const refreshedDetail: EvaluationNodeDetail = {
  ...initialDetail,
  timing: { ...initialDetail.timing, elapsed_ms: 1100 },
  events: [
    ...initialDetail.events,
    {
      id: 3,
      event_type: "tool_finished",
      from_status: null,
      to_status: null,
      attempt: 1,
      actor: "codex",
      message: "pytest -q",
      payload: { tool_name: "command_execution", exit_code: 0, output: "1 passed" },
      created_at: "2026-08-04T02:00:03+00:00",
    },
    {
      id: 4,
      event_type: "verifier_finished",
      from_status: null,
      to_status: null,
      attempt: 1,
      actor: "benchmark",
      message: "隐藏校验失败",
      payload: { sample_id: "pricing_total", passed: false, message: "AssertionError: total" },
      created_at: "2026-08-04T02:00:04+00:00",
    },
  ],
};

describe("EvaluationNodeInspector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEvaluationNodeSamples).mockResolvedValue({ samples: [], next_cursor: null });
  });

  it("shows Agent trace details and refreshes events when the running node advances", async () => {
    vi.mocked(getEvaluationNode)
      .mockResolvedValueOnce(initialDetail)
      .mockResolvedValueOnce(refreshedDetail);
    const { rerender } = render(
      <EvaluationNodeInspector
        taskId="task-agent"
        taskStatus="running"
        nodes={[runningNode]}
        retryingNodeId={null}
        onRetry={vi.fn()}
      />,
    );

    expect(await screen.findByText("Agent 实时过程")).toBeInTheDocument();
    expect(screen.getByText("Agent 开始处理")).toBeInTheDocument();
    expect(screen.getByText("Fix pricing.total_with_tax")).toBeInTheDocument();

    rerender(
      <EvaluationNodeInspector
        taskId="task-agent"
        taskStatus="running"
        nodes={[{ ...runningNode, timing: { ...runningNode.timing, elapsed_ms: 1100 } }]}
        retryingNodeId={null}
        onRetry={vi.fn()}
      />,
    );

    await waitFor(() => expect(getEvaluationNode).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("pytest -q")).toBeInTheDocument();
    expect(screen.getByText("1 passed")).toBeInTheDocument();
    expect(screen.getByText("AssertionError: total")).toBeInTheDocument();
  });

  it("renders every sample as a bilingual evidence ledger without hidden HumanEval material", async () => {
    const humaneval = node("node-humaneval", "hexagon-humaneval");
    vi.mocked(getEvaluationNode).mockResolvedValue(detail(humaneval));
    vi.mocked(getEvaluationNodeSamples).mockResolvedValue({
      samples: [
        {
          task_id: "task-1",
          node_id: humaneval.id,
          sample_key: "HumanEval/7",
          sample_index: 6,
          status: "failed",
          attempt_count: 1,
          input: { input: "English prompt", reference: "hidden canonical solution" },
          result: {
            score: 0,
            metadata: {
              input_zh: "中文辅助翻译",
              reference_zh: null,
              source: "HumanEval",
              source_key: "HumanEval/7",
            },
          },
          last_error: null,
          created_at: null,
          updated_at: null,
          finished_at: null,
        },
      ],
      next_cursor: null,
    });

    render(
      <EvaluationNodeInspector
        taskId="task-1"
        taskStatus="failed"
        nodes={[humaneval]}
        retryingNodeId={null}
        onRetry={vi.fn()}
      />,
    );

    expect(await screen.findByText("样本明细")).toBeInTheDocument();
    expect(screen.getByText("English prompt")).toBeInTheDocument();
    expect(screen.getByText("中文辅助翻译")).toBeInTheDocument();
    expect(screen.getByText("EvalHub 中文辅助翻译，非官方译文")).toBeInTheDocument();
    expect(screen.getByText("HumanEval/7")).toBeInTheDocument();
    expect(screen.queryByText("hidden canonical solution")).toBeNull();
    expect(getEvaluationNodeSamples).toHaveBeenCalledWith("task-1", humaneval.id, { limit: 20 });
  });
});
