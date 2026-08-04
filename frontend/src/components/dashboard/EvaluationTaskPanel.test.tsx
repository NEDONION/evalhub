import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { EvaluationTaskSummary, EvaluationType } from "../../types";
import { EvaluationTaskPanel } from "./EvaluationTaskPanel";

function taskFixture(id: string, evaluationType: EvaluationType): EvaluationTaskSummary {
  return {
    id,
    status: "success",
    evaluation_type: evaluationType,
    agent_framework: evaluationType === "agent" ? "pi" : null,
    dataset: evaluationType === "agent" ? "coding_mini" : "gsm8k",
    suite_id: null,
    model: evaluationType === "agent" ? "qwen-coder" : "qwen",
    adapter: "ollama",
    progress: { completed_samples: 3, total_samples: 3, percent: 100 },
    timing: {
      created_at: "2026-08-04T01:00:00+00:00",
      started_at: "2026-08-04T01:00:01+00:00",
      finished_at: "2026-08-04T01:01:00+00:00",
      elapsed_seconds: 60,
    },
    resources: {
      cpu: { current_percent: 0, peak_percent: 20 },
      memory: { current_bytes: 0, peak_bytes: 1024 },
      gpu: {
        supported: false,
        current_percent: null,
        peak_percent: null,
        current_memory_bytes: null,
        peak_memory_bytes: null,
      },
    },
    result_summary: {
      benchmark: evaluationType === "agent" ? "Coding Mini" : "GSM8K",
      total_samples: 3,
      passed_samples: 2,
      average_score: 0.67,
    },
    error_message: null,
  };
}

it("filters model and Agent jobs without mixing their task rows", async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(
    <EvaluationTaskPanel
      tasks={[taskFixture("model-task", "model"), taskFixture("agent-task", "agent")]}
      selectedTaskId="model-task"
      selectedTask={null}
      error={null}
      onSelect={onSelect}
      onCancel={vi.fn()}
      retryingNodeId={null}
      onRetryNode={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "模型评测 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Agent 评测 1" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Agent 评测 1" }));

  const agentRow = screen.getByRole("button", { name: "查看任务 agent-task" });
  expect(within(agentRow).getByText("Agent")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查看任务 model-task" })).not.toBeInTheDocument();
  expect(onSelect).toHaveBeenCalledWith("agent-task");
});

it("keeps the real FIFO queue position after filtering by task type", async () => {
  const user = userEvent.setup();
  const agentTask = {
    ...taskFixture("agent-pending", "agent"),
    status: "pending" as const,
    result_summary: null,
  };
  const modelTask = {
    ...taskFixture("model-running", "model"),
    status: "running" as const,
    result_summary: null,
  };
  render(
    <EvaluationTaskPanel
      tasks={[agentTask, modelTask]}
      selectedTaskId="agent-pending"
      selectedTask={null}
      error={null}
      onSelect={vi.fn()}
      onCancel={vi.fn()}
      retryingNodeId={null}
      onRetryNode={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: "Agent 评测 1" }));

  expect(within(screen.getByRole("button", { name: "查看任务 agent-pending" })).getByText(/前方 1 个/)).toBeInTheDocument();
});

it("keeps legacy tasks without an evaluation type in the model group", () => {
  const legacyTask = taskFixture("legacy-task", "model");
  legacyTask.evaluation_type = undefined;
  render(
    <EvaluationTaskPanel
      tasks={[legacyTask]}
      selectedTaskId="legacy-task"
      selectedTask={null}
      error={null}
      onSelect={vi.fn()}
      onCancel={vi.fn()}
      retryingNodeId={null}
      onRetryNode={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "模型评测 1" })).toBeInTheDocument();
  expect(within(screen.getByRole("button", { name: "查看任务 legacy-task" })).getByText("模型")).toBeInTheDocument();
});
