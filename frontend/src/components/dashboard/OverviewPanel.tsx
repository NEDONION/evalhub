import { ArrowRight, ChartNoAxesColumnIncreasing, PackageOpen, PlayCircle } from "lucide-react";

import type { WorkspaceView } from "./SidebarNav";
import { Panel } from "../ui/Panel";

interface OverviewPanelProps {
  onNavigate: (view: WorkspaceView) => void;
}

const actions = [
  {
    view: "evaluation" as const,
    icon: PlayCircle,
    title: "配置一次评测",
    description: "选择 Benchmark、模型和样本范围。",
    action: "新建一次评测",
  },
  {
    view: "assets" as const,
    icon: PackageOpen,
    title: "准备本地资产",
    description: "下载模型，缓存或更新公开数据集。",
    action: "管理本地资产",
  },
  {
    view: "results" as const,
    icon: ChartNoAxesColumnIncreasing,
    title: "检查运行输出",
    description: "查看得分、通过率和失败样本。",
    action: "查看评测结果",
  },
];

/**
 * 展示从评测配置、资产准备到结果检查的三个主要入口。
 *
 * @param onNavigate 点击工作流入口时切换到目标工作区的回调；组件自身不保存导航状态，
 * 从而让 `App` 继续作为目录与异步任务状态的唯一所有者。
 */
export function OverviewPanel({ onNavigate }: OverviewPanelProps) {
  return (
    <Panel aria-labelledby="workflow-title" className="overflow-hidden">
      <div className="border-b border-border px-5 py-5 sm:px-6">
        <p className="mb-1 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">Evaluation flow</p>
        <h2 id="workflow-title" className="text-base font-semibold tracking-tight text-ink">当前工作流</h2>
        <p className="mt-1 text-sm text-muted">从准备资产开始，运行评测后集中检查结果。</p>
      </div>
      <div className="grid lg:grid-cols-3">
        {actions.map(({ view, icon: Icon, title, description, action }, index) => (
          <article key={view} className="relative border-b border-border p-5 last:border-b-0 lg:border-r lg:border-b-0 lg:last:border-r-0 sm:p-6">
            <div className="flex items-start justify-between gap-4">
              <span className="grid h-9 w-9 place-items-center rounded-md border border-blue-200 bg-blue-50 text-primary">
                <Icon className="h-4 w-4" aria-hidden="true" />
              </span>
              <span className="font-mono text-[10px] font-medium text-slate-300">0{index + 1}</span>
            </div>
            <h3 className="mt-5 text-sm font-semibold text-ink">{title}</h3>
            <p className="mt-1 min-h-10 text-xs leading-5 text-muted">{description}</p>
            <button
              type="button"
              onClick={() => onNavigate(view)}
              className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {action}
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </article>
        ))}
      </div>
    </Panel>
  );
}
