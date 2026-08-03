import { Activity, Bot, Database, Trophy } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "../../lib/utils";

interface MetricStripProps {
  health: "loading" | "online" | "offline";
  ollamaState: "loading" | "ready" | "warning" | "offline";
  datasetCount: number;
  preparedCount: number;
  latestScore: string;
}

interface RailItemProps {
  icon: LucideIcon;
  label: string;
  value: string;
  state?: "neutral" | "ready" | "warning" | "offline";
  meta: string;
}

function RailItem({ icon: Icon, label, value, state = "neutral", meta }: RailItemProps) {
  return (
    <div className="relative min-w-0 px-4 py-4 sm:px-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-muted">{label}</span>
        <span
          className={cn(
            "grid h-7 w-7 place-items-center rounded-md border",
            state === "ready" && "border-blue-200 bg-blue-50 text-blue-600",
            state === "warning" && "border-amber-200 bg-amber-50 text-amber-600",
            state === "offline" && "border-red-200 bg-red-50 text-red-600",
            state === "neutral" && "border-border bg-slate-50 text-slate-500",
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
      </div>
      <strong className="block truncate text-lg font-semibold tracking-[-0.025em] text-ink">{value}</strong>
      <span className="mt-1 block truncate text-xs text-slate-400">{meta}</span>
    </div>
  );
}

export function MetricStrip({ health, ollamaState, datasetCount, preparedCount, latestScore }: MetricStripProps) {
  const healthValue = health === "online" ? "在线" : health === "offline" ? "异常" : "检测中";
  const ollamaValue =
    ollamaState === "ready" ? "已就绪" : ollamaState === "warning" ? "需处理" : ollamaState === "offline" ? "不可用" : "检测中";

  return (
    <section aria-label="评测就绪轨道" className="readiness-rail grid overflow-hidden rounded-lg border border-border bg-white sm:grid-cols-2 lg:grid-cols-4">
      <RailItem
        icon={Activity}
        label="EvalHub 服务"
        value={healthValue}
        state={health === "online" ? "ready" : health === "offline" ? "offline" : "neutral"}
        meta="本地 API"
      />
      <RailItem
        icon={Bot}
        label="Ollama"
        value={ollamaValue}
        state={ollamaState === "loading" ? "neutral" : ollamaState}
        meta="推理运行时"
      />
      <RailItem icon={Database} label="数据集资产" value={`${preparedCount} / ${datasetCount}`} meta="已缓存 / 全部" />
      <RailItem icon={Trophy} label="最近得分" value={latestScore} meta="平均分" />
    </section>
  );
}
