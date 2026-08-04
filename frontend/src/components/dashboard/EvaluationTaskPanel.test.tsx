import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type {
  EvaluationTaskDetail,
  EvaluationTaskSummary,
  EvaluationType,
} from "../../types";
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

/** 构造无需结果正文也能呈现任务进度与资源的完整详情。 */
function taskDetailFixture(id: string, evaluationType: EvaluationType): EvaluationTaskDetail {
  return {
    ...taskFixture(id, evaluationType),
    request: {
      evaluation_type: evaluationType,
      agent_framework: evaluationType === "agent" ? "pi" : undefined,
      agent_difficulty: evaluationType === "agent" ? "all" : undefined,
      dataset: evaluationType === "agent" ? "coding_mini" : "gsm8k",
      adapter: "ollama",
      model: evaluationType === "agent" ? "qwen-coder" : "qwen",
      base_url: "http://127.0.0.1:11434",
      sample_mode: "all",
    },
    result: null,
    nodes: [],
  };
}

it("opens the selected job in a dismissible side drawer", async () => {
  const user = userEvent.setup();
  render(
    <EvaluationTaskPanel
      tasks={[taskFixture("model-task", "model")]}
      selectedTaskId="model-task"
      selectedTask={taskDetailFixture("model-task", "model")}
      error={null}
      onSelect={vi.fn()}
      onCancel={vi.fn()}
      retryingNodeId={null}
      onRetryNode={vi.fn()}
    />,
  );

  expect(screen.queryByRole("dialog", { name: "任务详情" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看任务 model-task" }));

  expect(screen.getByRole("dialog", { name: "任务详情" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "关闭任务详情" }));
  expect(screen.queryByRole("dialog", { name: "任务详情" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看任务 model-task" })).toBeInTheDocument();
});

it("shows loading instead of stale details while the next job is being fetched", async () => {
  const user = userEvent.setup();
  const staleDetail = { ...taskDetailFixture("old-task", "model"), error_message: "旧任务错误" };
  render(
    <EvaluationTaskPanel
      tasks={[taskFixture("new-task", "model")]}
      selectedTaskId="new-task"
      selectedTask={staleDetail}
      error={null}
      onSelect={vi.fn()}
      onCancel={vi.fn()}
      retryingNodeId={null}
      onRetryNode={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: "查看任务 new-task" }));
  const drawer = screen.getByRole("dialog", { name: "任务详情" });
  expect(within(drawer).getByText("正在读取任务详情")).toBeInTheDocument();
  expect(within(drawer).queryByText("旧任务错误")).not.toBeInTheDocument();
});

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
