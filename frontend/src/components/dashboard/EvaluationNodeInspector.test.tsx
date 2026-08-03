import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

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
