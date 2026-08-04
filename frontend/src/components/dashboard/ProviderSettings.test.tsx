import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import {
  createModelProvider,
  deleteModelProvider,
  getModelProviders,
  testModelProvider,
  updateModelProvider,
} from "../../lib/api";
import type { ModelProvider } from "../../types";
import { ProviderSettings } from "./ProviderSettings";

vi.mock("../../lib/api", () => ({
  createModelProvider: vi.fn(),
  deleteModelProvider: vi.fn(),
  getModelProviders: vi.fn(),
  testModelProvider: vi.fn(),
  updateModelProvider: vi.fn(),
}));

const deepseek: ModelProvider = {
  id: "deepseek",
  name: "DeepSeek",
  kind: "builtin",
  base_url: "https://api.deepseek.com",
  key_configured: true,
  key_hint: "1234",
  created_at: "2026-08-05T00:00:00+00:00",
  updated_at: "2026-08-05T00:00:00+00:00",
};

const siliconflow: ModelProvider = {
  id: "siliconflow",
  name: "硅基流动",
  kind: "builtin",
  base_url: "https://api.siliconflow.cn/v1",
  key_configured: false,
  key_hint: null,
  created_at: null,
  updated_at: null,
};

function StatefulProviderSettings({
  onModelChange,
  onSelectionChange,
}: {
  onModelChange: (model: string) => void;
  onSelectionChange: (provider: ModelProvider | null) => void;
}) {
  const [model, setModel] = useState("");
  return (
    <ProviderSettings
      model={model}
      onModelChange={(nextModel) => {
        setModel(nextModel);
        onModelChange(nextModel);
      }}
      onSelectionChange={onSelectionChange}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getModelProviders).mockResolvedValue([deepseek, siliconflow]);
  vi.mocked(updateModelProvider).mockResolvedValue(deepseek);
  vi.mocked(testModelProvider).mockResolvedValue(["deepseek-v4-flash", "deepseek-v4-pro"]);
  vi.mocked(deleteModelProvider).mockResolvedValue({
    ok: true,
    provider_id: "deepseek",
    reset: true,
  });
});

it("keeps saved keys masked while updating and discovering models", async () => {
  const user = userEvent.setup();
  const onModelChange = vi.fn();
  const onSelectionChange = vi.fn();
  render(
    <StatefulProviderSettings
      onModelChange={onModelChange}
      onSelectionChange={onSelectionChange}
    />,
  );

  expect(await screen.findByLabelText("API 服务商")).toHaveValue("deepseek");
  expect(screen.getByText("已配置 · 尾号 1234")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "管理服务商" }));
  const password = screen.getByLabelText(/API Key/);
  expect(password).toHaveValue("");

  await user.click(screen.getByRole("button", { name: "保存并验证" }));

  await waitFor(() => {
    expect(updateModelProvider).toHaveBeenCalledWith("deepseek", {
      name: "DeepSeek",
      base_url: "https://api.deepseek.com",
      api_key: "",
    });
    expect(testModelProvider).toHaveBeenCalledWith("deepseek");
  });
  expect(password).toHaveValue("");
  expect(document.querySelector('datalist option[value="deepseek-v4-pro"]')).not.toBeNull();

  await user.type(screen.getByLabelText("模型 ID"), "deepseek-v4-pro");
  expect(onModelChange).toHaveBeenLastCalledWith("deepseek-v4-pro");
  expect(onSelectionChange).toHaveBeenCalledWith(deepseek);
});

it("creates a custom provider and never prefills its password", async () => {
  const user = userEvent.setup();
  const custom: ModelProvider = {
    ...deepseek,
    id: "provider_custom",
    name: "Internal Gateway",
    kind: "custom",
    base_url: "https://gateway.example.com/v1",
    key_hint: "cret",
  };
  vi.mocked(createModelProvider).mockResolvedValue(custom);
  vi.mocked(testModelProvider).mockResolvedValue(["private-model"]);
  render(
    <ProviderSettings model="" onModelChange={vi.fn()} onSelectionChange={vi.fn()} />,
  );

  await screen.findByLabelText("API 服务商");
  await user.click(screen.getByRole("button", { name: "管理服务商" }));
  await user.click(screen.getByRole("button", { name: "添加自定义" }));
  await user.type(screen.getByLabelText("服务商名称"), "Internal Gateway");
  await user.type(screen.getByLabelText("Base URL"), "https://gateway.example.com/v1");
  await user.type(screen.getByLabelText(/API Key/), "sk-new-secret");
  await user.click(screen.getByRole("button", { name: "保存并验证" }));

  await waitFor(() => {
    expect(createModelProvider).toHaveBeenCalledWith({
      name: "Internal Gateway",
      base_url: "https://gateway.example.com/v1",
      api_key: "sk-new-secret",
    });
  });
  expect(screen.getByLabelText(/API Key/)).toHaveValue("");
  expect(screen.queryByDisplayValue("sk-new-secret")).not.toBeInTheDocument();
});

it("keeps the model ID editable when discovery fails", async () => {
  const user = userEvent.setup();
  const onModelChange = vi.fn();
  vi.mocked(testModelProvider).mockRejectedValue(new Error("连接失败"));
  render(
    <StatefulProviderSettings
      onModelChange={onModelChange}
      onSelectionChange={vi.fn()}
    />,
  );

  await screen.findByLabelText("API 服务商");
  await user.click(screen.getByRole("button", { name: "管理服务商" }));
  await user.click(screen.getByRole("button", { name: "保存并验证" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("仍可手填模型 ID");

  await user.type(screen.getByLabelText("模型 ID"), "manual-model");
  expect(onModelChange).toHaveBeenLastCalledWith("manual-model");
});

it("confirms and deletes a custom provider before refreshing the list", async () => {
  const user = userEvent.setup();
  const custom: ModelProvider = {
    ...deepseek,
    id: "provider_custom",
    name: "Internal Gateway",
    kind: "custom",
  };
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(getModelProviders)
    .mockResolvedValueOnce([custom])
    .mockResolvedValueOnce([deepseek, siliconflow]);
  vi.mocked(deleteModelProvider).mockResolvedValue({
    ok: true,
    provider_id: custom.id,
    reset: false,
  });
  render(
    <ProviderSettings model="" onModelChange={vi.fn()} onSelectionChange={vi.fn()} />,
  );

  expect(await screen.findByLabelText("API 服务商")).toHaveValue(custom.id);
  await user.click(screen.getByRole("button", { name: "管理服务商" }));
  await user.click(screen.getByRole("button", { name: "删除服务商" }));

  await waitFor(() => expect(deleteModelProvider).toHaveBeenCalledWith(custom.id));
  expect(confirm).toHaveBeenCalledWith("确认删除 Internal Gateway？");
  expect(screen.getByLabelText("API 服务商")).toHaveValue("deepseek");
  confirm.mockRestore();
});
