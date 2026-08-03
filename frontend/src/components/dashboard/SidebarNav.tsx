import {
  ChartNoAxesColumnIncreasing,
  LayoutDashboard,
  PackageOpen,
  PlayCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "../../lib/utils";

export type WorkspaceView = "overview" | "evaluation" | "assets" | "results";

interface SidebarNavProps {
  currentView: WorkspaceView;
  onNavigate: (view: WorkspaceView) => void;
  modelPullActive: boolean;
  datasetPreparing: boolean;
  evaluationRunning: boolean;
  resultAvailable: boolean;
}

interface NavigationItem {
  view: WorkspaceView;
  label: string;
  description: string;
  icon: LucideIcon;
}

const navigationItems: NavigationItem[] = [
  { view: "overview", label: "概览", description: "环境与任务状态", icon: LayoutDashboard },
  { view: "evaluation", label: "发起评测", description: "配置 Benchmark 任务", icon: PlayCircle },
  { view: "assets", label: "资产管理", description: "模型与数据集", icon: PackageOpen },
  { view: "results", label: "评测结果", description: "得分与失败样本", icon: ChartNoAxesColumnIncreasing },
];

/**
 * 渲染桌面侧边栏与移动端横向目录，并用活动标签提示正在运行的工作。
 *
 * @param currentView 当前可见的工作区，用于设置活动样式和 `aria-current`。
 * @param onNavigate 用户选择目录时的视图切换回调。
 * @param modelPullActive 是否存在进行中的 Ollama 模型下载。
 * @param datasetPreparing 是否正在缓存或更新 Benchmark 数据集。
 * @param evaluationRunning 是否正在执行评测。
 * @param resultAvailable 当前会话是否已有可查看的评测结果。
 */
export function SidebarNav({
  currentView,
  onNavigate,
  modelPullActive,
  datasetPreparing,
  evaluationRunning,
  resultAvailable,
}: SidebarNavProps) {
  const activity: Partial<Record<WorkspaceView, string>> = {
    evaluation: evaluationRunning ? "运行中" : undefined,
    assets: modelPullActive ? "下载中" : datasetPreparing ? "更新中" : undefined,
    results: resultAvailable ? "最新" : undefined,
  };

  return (
    <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start">
      <nav
        aria-label="工作区目录"
        className="overflow-x-auto rounded-lg border border-border bg-white p-2 shadow-[0_1px_2px_rgba(15,23,42,0.035)] lg:overflow-visible lg:p-3"
      >
        <div className="hidden px-3 pt-2 pb-3 lg:block">
          <p className="text-[10px] font-semibold tracking-[0.14em] text-slate-400 uppercase">Workspace</p>
          <p className="mt-1 text-xs leading-5 text-muted">按任务切换，不中断当前进度。</p>
        </div>
        <div className="flex min-w-max gap-1 lg:min-w-0 lg:flex-col">
          {navigationItems.map(({ view, label, description, icon: Icon }) => {
            const active = currentView === view;
            const status = activity[view];
            return (
              <button
                key={view}
                type="button"
                aria-label={`打开${label}页面`}
                aria-current={active ? "page" : undefined}
                onClick={() => onNavigate(view)}
                className={cn(
                  "group relative flex min-h-11 min-w-28 items-center gap-3 rounded-md px-3 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary lg:min-w-0 lg:w-full",
                  active ? "bg-blue-50 text-primary" : "text-muted hover:bg-slate-50 hover:text-ink",
                )}
              >
                <span
                  className={cn(
                    "absolute top-2 bottom-2 left-0 hidden w-0.5 rounded-full lg:block",
                    active && "bg-primary",
                  )}
                  aria-hidden="true"
                />
                <span
                  className={cn(
                    "grid h-8 w-8 shrink-0 place-items-center rounded-md border",
                    active ? "border-blue-200 bg-white text-primary" : "border-border bg-slate-50 text-slate-500",
                  )}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-semibold">{label}</span>
                  <span className="mt-0.5 hidden truncate text-[11px] text-slate-400 lg:block">{description}</span>
                </span>
                {status ? (
                  <span className="ml-auto hidden rounded-full border border-blue-200 bg-white px-2 py-0.5 text-[10px] font-medium text-blue-700 xl:inline">
                    {status}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </nav>
    </aside>
  );
}
