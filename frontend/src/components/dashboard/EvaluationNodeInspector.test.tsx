import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getEvaluationNode, getEvaluationNodeSamples } from "../../lib/api";
import type { EvaluationNodeDetail, EvaluationNodeSummary } from "../../types";
import { EvaluationNodeInspector } from "./EvaluationNodeInspector";

vi.mock("../../lib/api", () => ({
  getEvaluationNode: vi.fn(),
  getEvaluationNodeSamples: vi.fn(),
}));

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
      id: 2,
      event_type: "tool_finished",
      from_status: null,
      to_status: null,
      attempt: 1,
      actor: "codex",
      message: "pytest -q",
      payload: { tool_name: "command_execution", exit_code: 0, output: "1 passed" },
      created_at: "2026-08-04T02:00:03+00:00",
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
  });
});
