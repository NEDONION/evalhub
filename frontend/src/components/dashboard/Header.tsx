import { RefreshCw } from "lucide-react";

import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";

interface HeaderProps {
  health: "loading" | "online" | "offline";
  refreshing: boolean;
  onRefresh: () => void;
}

export function Header({ health, refreshing, onRefresh }: HeaderProps) {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-white/95 backdrop-blur-sm">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <div
            className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-primary text-xs font-bold tracking-[-0.03em] text-white shadow-[0_6px_18px_rgba(37,99,235,0.22)]"
            aria-hidden="true"
          >
            EH
          </div>
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-semibold tracking-tight text-ink">EvalHub</span>
              <span className="hidden text-xs text-muted sm:inline">本地评测控制台</span>
            </div>
            <p className="truncate text-[11px] font-medium tracking-[0.08em] text-slate-400 uppercase">
              Local evaluation workspace
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Badge tone="info" dot>
            本地环境
          </Badge>
          <Badge
            tone={health === "online" ? "success" : health === "offline" ? "danger" : "neutral"}
            dot
            className="hidden sm:inline-flex"
          >
            {health === "online" ? "服务在线" : health === "offline" ? "服务异常" : "检测中"}
          </Badge>
          <Button variant="secondary" size="sm" onClick={onRefresh} disabled={refreshing} aria-label="刷新状态">
            <RefreshCw className={refreshing ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} aria-hidden="true" />
            <span className="hidden sm:inline">刷新</span>
          </Button>
        </div>
      </div>
    </header>
  );
}
