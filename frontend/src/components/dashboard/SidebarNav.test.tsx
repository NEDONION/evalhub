import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { SidebarNav } from "./SidebarNav";

it("keeps activity badges on one line when the sidebar is busy", () => {
  render(
    <SidebarNav
      currentView="overview"
      onNavigate={vi.fn()}
      modelPullActive={false}
      datasetPreparing={false}
      evaluationRunning
      resultAvailable
    />,
  );

  expect(screen.getByText("运行中")).toHaveClass("shrink-0", "whitespace-nowrap");
  expect(screen.getByText("最新")).toHaveClass("shrink-0", "whitespace-nowrap");
  expect(screen.getByRole("button", { name: "打开模型成绩页面" })).toBeInTheDocument();
});
