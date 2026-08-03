import { Bot, Box, Command, RadioTower } from "lucide-react";

import type { OllamaStatus } from "../../types";
import { Badge } from "../ui/Badge";
import { Panel } from "../ui/Panel";

interface OllamaPanelProps {
  status: OllamaStatus | null;
  loading: boolean;
  error: string | null;
}

function statusPresentation(status: OllamaStatus | null, loading: boolean) {
  if (loading && !status) return { label: "检测中", tone: "neutral" as const };
  if (!status?.installed) return { label: "未安装", tone: "danger" as const };
  if (!status.running) return { label: "未启动", tone: "warning" as const };
  if (!status.model_present) return { label: "缺少模型", tone: "warning" as const };
  return { label: "已就绪", tone: "success" as const };
}

export function OllamaPanel({ status, loading, error }: OllamaPanelProps) {
  const presentation = statusPresentation(status, loading);
  const facts = [
    { icon: Command, label: "命令", value: status?.command || "未检测到" },
    { icon: RadioTower, label: "服务地址", value: status?.base_url || "http://127.0.0.1:11434" },
    { icon: Bot, label: "目标模型", value: status?.model || "qwen2.5:0.5b" },
    { icon: Box, label: "本地模型", value: status ? `${status.models.length} 个` : "—" },
  ];

  return (
    <Panel aria-labelledby="ollama-title" className="overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-border px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div>
          <p className="mb-1 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">Runtime readiness</p>
          <h2 id="ollama-title" className="text-base font-semibold tracking-tight text-ink">
            本地推理环境
          </h2>
          <p className="mt-1 text-sm text-muted">执行评测前，确认命令、服务和目标模型均可用。</p>
        </div>
        <Badge tone={error ? "danger" : presentation.tone} dot>
          {error ? "检测失败" : presentation.label}
        </Badge>
      </div>

      <dl className="grid sm:grid-cols-2 xl:grid-cols-4">
        {facts.map(({ icon: Icon, label, value }) => (
          <div key={label} className="min-w-0 border-b border-border px-5 py-4 last:border-b-0 sm:border-r sm:nth-[2n]:border-r-0 xl:border-b-0 xl:nth-[2n]:border-r xl:last:border-r-0 sm:px-6">
            <dt className="flex items-center gap-2 text-xs font-medium text-muted">
              <Icon className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
              {label}
            </dt>
            <dd className="mt-2 truncate font-mono text-xs font-medium text-ink" title={value}>
              {value}
            </dd>
          </div>
        ))}
      </dl>

      <div
        className={error ? "border-t border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700 sm:px-6" : "border-t border-blue-100 bg-blue-50/70 px-5 py-3 text-sm text-blue-800 sm:px-6"}
        role={error ? "alert" : "status"}
      >
        {error || status?.message || "正在检测 Ollama。"}
      </div>
    </Panel>
  );
}
