import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ModelOption } from "../../types";
import { ModelSelector } from "./ModelSelector";

const options: ModelOption[] = [
  {
    name: "granite4.1:3b",
    label: "Granite 4.1 3B",
    description: "轻量 Agent 基线",
    installed: true,
    size_bytes: 2_100_000_000,
    size_kind: "actual",
    evaluation_types: ["model", "agent"],
    capability_label: "Agent 基线",
  },
  {
    name: "ministral-3:8b",
    label: "Ministral 3 8B",
    description: "紧凑工具调用模型",
    installed: false,
    size_bytes: 6_000_000_000,
    size_kind: "estimated",
    evaluation_types: ["model", "agent"],
    capability_label: "Agent 工具",
  },
];

describe("ModelSelector", () => {
  it("按安装状态分组并通过点击选择模型", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelSelector id="model" label="Agent 基模" options={options} value="granite4.1:3b" onChange={onChange} />,
    );

    const trigger = screen.getByRole("button", { name: "Agent 基模" });
    await user.click(trigger);

    expect(screen.getByText("已安装")).toBeInTheDocument();
    expect(screen.getByText("推荐下载")).toBeInTheDocument();
    const recommended = screen.getByRole("option", { name: /Ministral 3 8B/ });
    expect(recommended).toHaveTextContent("约 6.0 GB");
    await user.click(recommended);

    expect(onChange).toHaveBeenCalledWith("ministral-3:8b");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("支持方向键、首尾跳转、确认和退出", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelSelector id="model" label="模型" options={options} value="granite4.1:3b" onChange={onChange} />,
    );

    const trigger = screen.getByRole("button", { name: "模型" });
    trigger.focus();
    await user.keyboard("{ArrowDown}{End}{Enter}");
    expect(onChange).toHaveBeenCalledWith("ministral-3:8b");

    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("没有候选模型时给出不可操作的空状态", () => {
    render(<ModelSelector id="model" label="模型" options={[]} value="" onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "模型" })).toBeDisabled();
    expect(screen.getByText("暂无可用模型")).toBeInTheDocument();
  });
});
