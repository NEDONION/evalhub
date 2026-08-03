import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ModelPerformanceResponse } from "../../types";
import { ModelPerformancePanel } from "./ModelPerformancePanel";

const report: ModelPerformanceResponse = {
  scopes: [
    {
      key: "benchmark:gsm8k",
      kind: "benchmark",
      id: "gsm8k",
      label: "GSM8K",
      run_count: 5,
    },
    {
      key: "suite:core-v1",
      kind: "suite",
      id: "core-v1",
      label: "core-v1",
      run_count: 2,
    },
  ],
  selected_scope: {
    key: "benchmark:gsm8k",
    kind: "benchmark",
    id: "gsm8k",
    label: "GSM8K",
    run_count: 5,
  },
  models: [
    {
      model: "qwen2.5:1.5b",
      best_score: 0.92,
      latest_score: 0.92,
      run_count: 3,
      best_task_id: "task-qwen-3",
      best_at: "2026-08-04T04:00:00+00:00",
      latest_at: "2026-08-04T04:00:00+00:00",
      history: [
        {
          scope_key: "benchmark:gsm8k",
          task_id: "task-qwen-1",
          model: "qwen2.5:1.5b",
          score: 0.7,
          completed_at: "2026-08-04T02:00:00+00:00",
          is_record: false,
          improvement: null,
        },
        {
          scope_key: "benchmark:gsm8k",
          task_id: "task-qwen-2",
          model: "qwen2.5:1.5b",
          score: 0.8,
          completed_at: "2026-08-04T03:00:00+00:00",
          is_record: true,
          improvement: 0.1,
        },
        {
          scope_key: "benchmark:gsm8k",
          task_id: "task-qwen-3",
          model: "qwen2.5:1.5b",
          score: 0.92,
          completed_at: "2026-08-04T04:00:00+00:00",
          is_record: true,
          improvement: 0.12,
        },
      ],
    },
    {
      model: "llama3.2:1b",
      best_score: 0.76,
      latest_score: 0.76,
      run_count: 2,
      best_task_id: "task-llama-2",
      best_at: "2026-08-04T03:30:00+00:00",
      latest_at: "2026-08-04T03:30:00+00:00",
      history: [
        {
          scope_key: "benchmark:gsm8k",
          task_id: "task-llama-1",
          model: "llama3.2:1b",
          score: 0.72,
          completed_at: "2026-08-04T02:30:00+00:00",
          is_record: false,
          improvement: null,
        },
        {
          scope_key: "benchmark:gsm8k",
          task_id: "task-llama-2",
          model: "llama3.2:1b",
          score: 0.76,
          completed_at: "2026-08-04T03:30:00+00:00",
          is_record: true,
          improvement: 0.04,
        },
      ],
    },
  ],
  record: {
    scope_key: "benchmark:gsm8k",
    task_id: "task-qwen-3",
    model: "qwen2.5:1.5b",
    score: 0.92,
    completed_at: "2026-08-04T04:00:00+00:00",
    is_record: true,
    improvement: 0.12,
  },
};

describe("ModelPerformancePanel", () => {
  it("compares ranked models and switches the selected trend", () => {
    const onScopeChange = vi.fn();
    render(
      <ModelPerformancePanel
        report={report}
        loading={false}
        error={null}
        onScopeChange={onScopeChange}
        onStartEvaluation={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "模型历史成绩" })).toBeInTheDocument();
    const leaderboard = screen.getByRole("region", { name: "模型排行榜" });
    const modelButtons = within(leaderboard).getAllByRole("button");
    expect(modelButtons[0]).toHaveAccessibleName(/qwen2\.5:1\.5b/);
    expect(modelButtons[1]).toHaveAccessibleName(/llama3\.2:1b/);
    expect(screen.getByRole("img", { name: "qwen2.5:1.5b 历史成绩趋势" })).toBeInTheDocument();
    expect(screen.getByText("刷新纪录 +12.0%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /第 2 名 llama3\.2:1b/ }));

    expect(screen.getByRole("img", { name: "llama3.2:1b 历史成绩趋势" })).toBeInTheDocument();
  });

  it("requests a new comparison scope", () => {
    const onScopeChange = vi.fn();
    render(
      <ModelPerformancePanel
        report={report}
        loading={false}
        error={null}
        onScopeChange={onScopeChange}
        onStartEvaluation={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("比较范围"), { target: { value: "suite:core-v1" } });

    expect(onScopeChange).toHaveBeenCalledWith("suite:core-v1");
  });

  it("keeps prior rankings visible during a recoverable refresh error", () => {
    render(
      <ModelPerformancePanel
        report={report}
        loading={false}
        error="暂时无法刷新"
        onScopeChange={vi.fn()}
        onStartEvaluation={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("暂时无法刷新");
    expect(screen.getByRole("region", { name: "模型排行榜" })).toBeInTheDocument();
  });

  it("does not present an initial load failure as a successful empty history", () => {
    render(
      <ModelPerformancePanel
        report={null}
        loading={false}
        error="暂时无法读取模型成绩"
        onScopeChange={vi.fn()}
        onStartEvaluation={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("暂时无法读取模型成绩");
    expect(screen.queryByText("还没有可比较的模型成绩")).not.toBeInTheDocument();
  });

  it("guides an empty workspace to start a model evaluation", () => {
    const onStartEvaluation = vi.fn();
    render(
      <ModelPerformancePanel
        report={{ scopes: [], selected_scope: null, models: [], record: null }}
        loading={false}
        error={null}
        onScopeChange={vi.fn()}
        onStartEvaluation={onStartEvaluation}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "发起模型评测" }));

    expect(onStartEvaluation).toHaveBeenCalledTimes(1);
  });
});
