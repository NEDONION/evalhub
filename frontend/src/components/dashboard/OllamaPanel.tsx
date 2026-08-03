import { Bot, Box, Command, Download, RadioTower, X } from "lucide-react";

import { estimateDownloadRange, formatBytes, formatDuration, formatRate } from "../../lib/assets";
import type { ModelOption, OllamaPullTask, OllamaStatus } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";

interface OllamaPanelProps {
  status: OllamaStatus | null;
  loading: boolean;
  error: string | null;
  modelOption: ModelOption | null;
  pullTask: OllamaPullTask | null;
  pullError: string | null;
  downloadDismissed: boolean;
  onDownload: (model: string) => void;
  onCancel: (model: string) => void;
  onDecline: (model: string) => void;
}

/**
 * 把 Ollama 探测结果归一化为控制台徽标文案和视觉语气。
 *
 * @param status 后端返回的 Ollama 安装、服务和目标模型状态；尚未返回时为 `null`。
 * @param loading 当前是否正在刷新状态。
 * @returns 适用于 `Badge` 的中文标签与颜色语气。
 */
function statusPresentation(status: OllamaStatus | null, loading: boolean) {
  if (loading && !status) return { label: "检测中", tone: "neutral" as const };
  if (!status?.installed) return { label: "未安装", tone: "danger" as const };
  if (!status.running) return { label: "未启动", tone: "warning" as const };
  if (!status.model_present) return { label: "缺少模型", tone: "warning" as const };
  return { label: "已就绪", tone: "success" as const };
}

const activePullStatuses = new Set(["pending", "pulling", "verifying"]);

/**
 * 展示 Ollama 运行时详情，并在目标模型缺失时提供明确的下载选择和实时传输遥测。
 *
 * @param status Ollama 安装、运行和本地模型状态。
 * @param loading 是否正在重新探测运行时。
 * @param error 运行时探测失败信息。
 * @param modelOption 当前选择模型的安装状态和容量信息。
 * @param pullTask 当前服务端模型下载任务快照。
 * @param pullError 启动、查询或取消下载时的错误信息。
 * @param downloadDismissed 用户是否已对当前模型选择“暂不下载”。
 * @param onDownload 启动模型下载的回调。
 * @param onCancel 取消活动下载的回调。
 * @param onDecline 拒绝本次下载选择的回调。
 */
export function OllamaPanel({
  status,
  loading,
  error,
  modelOption,
  pullTask,
  pullError,
  downloadDismissed,
  onDownload,
  onCancel,
  onDecline,
}: OllamaPanelProps) {
  const presentation = statusPresentation(status, loading);
  const task = pullTask?.model === modelOption?.name ? pullTask : null;
  const pullActive = Boolean(task && activePullStatuses.has(task.status));
  const progress = task?.total_bytes
    ? Math.min(100, Math.round(((task.completed_bytes || 0) / task.total_bytes) * 100))
    : 0;
  const downloadRange = estimateDownloadRange(modelOption?.size_bytes);
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

      {modelOption && !modelOption.installed && !downloadDismissed ? (
        <section className="border-t border-amber-200 bg-amber-50/65 px-5 py-4 sm:px-6" aria-label="模型下载">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-amber-950">{modelOption.name} 尚未下载</p>
              <p className="mt-1 text-xs leading-5 text-amber-800">
                <span className="font-mono font-semibold">
                  {modelOption.size_kind === "estimated" ? "约 " : ""}{formatBytes(modelOption.size_bytes)}
                </span>
                {downloadRange ? (
                  <> · 按 20–100 Mbps 预计 {formatDuration(downloadRange.minimumSeconds)}–{formatDuration(downloadRange.maximumSeconds)}</>
                ) : null}
              </p>
            </div>

            {!pullActive ? (
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button size="sm" aria-label={`下载 ${modelOption.name}`} onClick={() => onDownload(modelOption.name)}>
                  <Download className="h-3.5 w-3.5" aria-hidden="true" />
                  下载模型
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`暂不下载 ${modelOption.name}`}
                  onClick={() => onDecline(modelOption.name)}
                >
                  暂不下载
                </Button>
              </div>
            ) : null}
          </div>

          {pullActive && task ? (
            <div className="mt-4 rounded-md border border-amber-200 bg-white/80 p-3">
              <div className="mb-2 flex items-center justify-between gap-3 text-xs">
                <span className="font-medium text-amber-950">{task.status === "verifying" ? "正在校验模型" : "正在下载模型"}</span>
                <span className="font-mono font-semibold text-amber-900">{progress}%</span>
              </div>
              <div
                role="progressbar"
                aria-label={`${modelOption.name} 下载进度`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={progress}
                className="h-2 overflow-hidden rounded-full bg-amber-100"
              >
                <div
                  className="h-full rounded-full bg-primary transition-[width] motion-reduce:transition-none"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-3 flex flex-col gap-2 text-xs text-amber-900 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono">
                  <span>{formatBytes(task.completed_bytes)} / {formatBytes(task.total_bytes)}</span>
                  <span>{formatRate(task.speed_bytes_per_second)}</span>
                  <span>预计剩余 {formatDuration(task.eta_seconds)}</span>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="self-start text-amber-900 hover:bg-amber-100 sm:self-auto"
                  aria-label={`取消 ${modelOption.name} 下载`}
                  onClick={() => onCancel(modelOption.name)}
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                  取消
                </Button>
              </div>
            </div>
          ) : null}

          {task && !pullActive && task.status !== "success" ? (
            <p className="mt-3 text-xs text-amber-900" role="status">{task.message}</p>
          ) : null}
        </section>
      ) : null}

      <div
        className={error || pullError ? "border-t border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700 sm:px-6" : "border-t border-blue-100 bg-blue-50/70 px-5 py-3 text-sm text-blue-800 sm:px-6"}
        role={error || pullError ? "alert" : "status"}
      >
        {error || pullError || status?.message || "正在检测 Ollama。"}
      </div>
    </Panel>
  );
}
