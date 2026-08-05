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
    benchmark_protocol: "verified",
    benchmark_protocol_reason: "模型已安装且生成协议已注册。",
    benchmark_protocol_version: "ollama-generate-v1",
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
    benchmark_protocol: "static_only",
    benchmark_protocol_reason: "生成协议已静态校验；模型未安装。",
    benchmark_protocol_version: "ollama-generate-v1",
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
    expect(recommended).toHaveTextContent("协议待实测");
    await user.click(recommended);

    expect(onChange).toHaveBeenCalledWith("ministral-3:8b");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("展示已验证与不支持的 Benchmark 协议状态", async () => {
    const user = userEvent.setup();
    const unsupported: ModelOption = {
      ...options[0]!,
      name: "custom-local:latest",
      label: "Custom Local",
      benchmark_protocol: "unsupported",
      benchmark_protocol_reason: "该模型未注册 Benchmark 生成协议。",
      benchmark_protocol_version: null,
    };
    render(
      <ModelSelector id="model" label="模型" options={[...options, unsupported]} value="granite4.1:3b" onChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "模型" }));

    expect(screen.getByRole("option", { name: /Granite 4.1 3B/ })).toHaveTextContent("Benchmark 已适配");
    expect(screen.getByRole("option", { name: /Custom Local/ })).toHaveTextContent("Benchmark 不支持");
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

  it("触发器下方空间不足时向上展开", async () => {
    const user = userEvent.setup();
    render(
      <ModelSelector id="model" label="模型" options={options} value="granite4.1:3b" onChange={vi.fn()} />,
    );

    const trigger = screen.getByRole("button", { name: "模型" });
    vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
      top: 680,
      bottom: 736,
      left: 20,
      right: 320,
      width: 300,
      height: 56,
      x: 20,
      y: 680,
      toJSON: () => ({}),
    });
    await user.click(trigger);

    expect(screen.getByRole("listbox")).toHaveClass("bottom-full");
  });
});
