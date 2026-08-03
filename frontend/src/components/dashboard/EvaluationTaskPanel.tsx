import {
  Activity,
  AlertTriangle,
  ChevronRight,
  CircleDashed,
  Clock3,
  Cpu,
  ListChecks,
  MemoryStick,
  X,
} from "lucide-react";
import type { JSX } from "react";

import type { EvaluationTaskDetail, EvaluationTaskStatus, EvaluationTaskSummary } from "../../types";
import { Badge, type BadgeProps } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import { EvaluationResultDetail } from "./EvaluationResultDetail";
import { EvaluationNodeInspector } from "./EvaluationNodeInspector";

interface EvaluationTaskPanelProps {
  tasks: EvaluationTaskSummary[];
  selectedTaskId: string | null;
  selectedTask: EvaluationTaskDetail | null;
  error: string | null;
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  retryingNodeId: string | null;
  onRetryNode: (taskId: string, nodeId: string) => Promise<unknown> | void;
}

const statusLabels: Record<EvaluationTaskStatus, string> = {
  pending: "排队中",
  running: "运行中",
  success: "已完成",
  failed: "失败",
  canceled: "已取消",
};

const statusTones: Record<EvaluationTaskStatus, BadgeProps["tone"]> = {
  pending: "neutral",
  running: "info",
  success: "success",
  failed: "danger",
  canceled: "warning",
};

/**
 * 展示持久化评测任务列表及当前任务详情，并将选择和取消操作交还页面状态层。
 *
 * @param props 任务摘要、当前详情、错误状态以及用户操作回调。
 * @returns 包含空状态、活动进度、资源遥测和结果详情的任务中心面板。
 */
export function EvaluationTaskPanel({
  tasks,
  selectedTaskId,
  selectedTask,
  error,
  onSelect,
  onCancel,
  retryingNodeId,
  onRetryNode,
}: EvaluationTaskPanelProps): JSX.Element {
  const activeCount = tasks.filter((task) => task.status === "pending" || task.status === "running").length;
  const selectedIndex = tasks.findIndex((task) => task.id === selectedTaskId);
  const selectedTasksAhead = countActiveTasksAhead(tasks, selectedIndex);

  return (
    <Panel aria-labelledby="task-panel-title" className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-border px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div>
          <p className="mb-1 flex items-center gap-2 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">
            <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
            Evaluation jobs
          </p>
          <h2 id="task-panel-title" className="text-base font-semibold tracking-tight text-ink">
            评测任务
          </h2>
          <p className="mt-1 text-sm text-muted">先追踪执行状态，选择任务后再查看评测结果。</p>
        </div>
        <Badge tone={activeCount > 0 ? "info" : "neutral"} dot={activeCount > 0}>
          {activeCount > 0 ? `${activeCount} 个活动任务` : `${tasks.length} 个历史任务`}
        </Badge>
      </div>

      {error ? (
        <div role="alert" className="flex items-start gap-3 border-b border-red-100 bg-red-50 px-5 py-4 text-sm text-red-700 sm:px-6">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <strong className="block font-semibold">任务状态暂时无法更新</strong>
            <span className="mt-1 block leading-5">{error}</span>
          </div>
        </div>
      ) : null}

      {tasks.length === 0 ? (
        <div className="grid min-h-48 place-items-center px-5 py-10 text-center sm:px-6">
          <div>
            <div className="relative mx-auto grid h-12 w-12 place-items-center rounded-lg border border-blue-200 bg-blue-50 text-primary">
              <ListChecks className="h-5 w-5" aria-hidden="true" />
              <span className="absolute -right-1 -bottom-1 h-3 w-3 rounded-sm border-2 border-white bg-blue-500" aria-hidden="true" />
            </div>
            <strong className="mt-4 block text-sm font-semibold text-ink">尚无评测任务</strong>
            <p className="mt-1 text-sm text-muted">提交评测后，可在这里追踪进度和资源占用。</p>
          </div>
        </div>
      ) : (
        <>
          <div className="divide-y divide-border border-b border-border" aria-label="评测任务列表">
            {tasks.map((task, index) => (
              <TaskRow
                key={task.id}
                task={task}
                tasksAhead={countActiveTasksAhead(tasks, index)}
                selected={task.id === selectedTaskId}
                onSelect={onSelect}
              />
            ))}
          </div>

          {selectedTask ? (
            <TaskDetail
              task={selectedTask}
              tasksAhead={selectedTasksAhead}
              retryingNodeId={retryingNodeId}
              onCancel={onCancel}
              onRetryNode={onRetryNode}
            />
          ) : (
            <div className="flex min-h-28 items-center justify-center gap-2 px-5 py-8 text-sm text-muted">
              <CircleDashed className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
              正在读取任务详情
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

interface TaskRowProps {
  task: EvaluationTaskSummary;
  tasksAhead: number;
  selected: boolean;
  onSelect: (taskId: string) => void;
}

/**
 * 渲染一条可键盘选择的任务摘要，集中展示状态、模型、进度和耗时。
 *
 * @param props 单个任务摘要、选中状态与选择回调。
 * @returns 具有按钮语义和当前项标记的任务行。
 */
function TaskRow({ task, tasksAhead, selected, onSelect }: TaskRowProps): JSX.Element {
  const isActive = task.status === "pending" || task.status === "running";
  return (
    <button
      type="button"
      aria-label={`查看任务 ${task.id}`}
      aria-current={selected ? "true" : undefined}
      onClick={() => onSelect(task.id)}
      className={`task-row grid w-full min-w-0 gap-3 px-5 py-4 text-left transition-colors sm:px-6 ${selected ? "task-row-selected" : "hover:bg-slate-50/75"}`}
    >
      <div className="flex min-w-0 items-center gap-3">
        <Badge tone={statusTones[task.status]} dot={isActive}>
          {statusLabels[task.status]}
        </Badge>
        <div className="min-w-0">
          <strong className="block truncate text-sm font-semibold text-ink">
            {task.suite_id ? "LLM 行业能力套件" : task.dataset.toUpperCase()}
          </strong>
          <span className="mt-0.5 block truncate font-mono text-[11px] text-slate-400">
            {task.model}
            {task.status === "pending" ? ` · ${tasksAhead > 0 ? `前方 ${tasksAhead} 个` : "即将开始"}` : ""}
          </span>
        </div>
      </div>
      <div className="min-w-0 self-center">
        <TaskTrack task={task} compact />
      </div>
      <div className="flex items-center justify-between gap-3 sm:justify-end">
        <span className="font-mono text-xs tabular-nums text-muted">{formatDuration(task.timing.elapsed_seconds)}</span>
        <ChevronRight className={`h-4 w-4 ${selected ? "text-primary" : "text-slate-300"}`} aria-hidden="true" />
      </div>
    </button>
  );
}

interface TaskDetailProps {
  task: EvaluationTaskDetail;
  tasksAhead: number;
  onCancel: (taskId: string) => void;
  retryingNodeId: string | null;
  onRetryNode: (taskId: string, nodeId: string) => Promise<unknown> | void;
}

/**
 * 渲染选中任务的运行轨道、进程资源、失败信息和可选完整结果。
 *
 * @param props 当前任务详情与取消操作回调。
 * @returns 随任务状态变化的详情区域；只有活动任务提供取消入口。
 */
function TaskDetail({
  task,
  tasksAhead,
  retryingNodeId,
  onCancel,
  onRetryNode,
}: TaskDetailProps): JSX.Element {
  const canCancel = task.status === "pending" || task.status === "running";
  const waitingForResources = task.status === "pending";
  return (
    <div aria-live="polite">
      <div className="px-5 py-5 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold tracking-[0.12em] text-slate-400 uppercase">Task detail</p>
            <h3 className="mt-1 text-sm font-semibold text-ink">任务详情</h3>
            <code className="mt-1 block truncate font-mono text-[11px] text-muted">{task.id}</code>
          </div>
          {canCancel ? (
            <Button variant="secondary" size="sm" onClick={() => onCancel(task.id)} aria-label={`取消任务 ${task.id}`}>
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              取消任务
            </Button>
          ) : null}
        </div>

        <div className="mt-5 rounded-lg border border-blue-100 bg-blue-50/45 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-xs font-medium text-blue-900">
              <Activity className="h-3.5 w-3.5" aria-hidden="true" />
              {task.status === "pending"
                ? `等待 Worker · ${tasksAhead > 0 ? `前方 ${tasksAhead} 个任务` : "即将开始"}`
                : task.status === "running"
                  ? "正在评测样本"
                  : "运行轨道"}
            </span>
            <span className="font-mono text-xs font-semibold tabular-nums text-blue-900">
              {task.progress.total_samples > 0
                ? `${task.progress.completed_samples} / ${task.progress.total_samples}`
                : "等待样本加载"}
            </span>
          </div>
          <TaskTrack task={task} />
        </div>
      </div>

      <section aria-label="任务资源" className="grid border-t border-border sm:grid-cols-2 lg:grid-cols-4">
        <TelemetryCard
          icon={Clock3}
          label="总耗时"
          value={formatDuration(task.timing.elapsed_seconds)}
          meta={task.status === "running" ? "包含排队与运行时间" : "任务生命周期"}
        />
        <TelemetryCard
          icon={Cpu}
          label="CPU"
          value={waitingForResources ? "—" : formatPercent(task.resources.cpu.current_percent)}
          meta={
            waitingForResources
              ? "等待任务启动"
              : task.adapter === "ollama"
                ? `本机峰值 ${formatPercent(task.resources.cpu.peak_percent)} · 含 Ollama`
                : `任务进程峰值 ${formatPercent(task.resources.cpu.peak_percent)}`
          }
        />
        <TelemetryCard
          icon={MemoryStick}
          label="内存"
          value={waitingForResources ? "—" : formatBytes(task.resources.memory.current_bytes)}
          meta={
            waitingForResources
              ? "等待任务启动"
              : `任务进程峰值 ${formatBytes(task.resources.memory.peak_bytes)}`
          }
        />
        <TelemetryCard
          icon={Activity}
          label="GPU"
          value={
            waitingForResources
              ? "—"
              : task.resources.gpu.supported
                ? formatPercent(task.resources.gpu.current_percent)
                : "不可用"
          }
          meta={
            waitingForResources
              ? "等待任务启动"
              : task.resources.gpu.supported
                ? `系统级 · 设备内存 ${formatBytes(task.resources.gpu.current_memory_bytes)}`
                : "当前平台未提供可靠指标"
          }
        />
      </section>

      {task.error_message ? (
        <div role="alert" className="flex items-start gap-3 border-t border-red-100 bg-red-50 px-5 py-4 text-sm text-red-700 sm:px-6">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <strong className="block font-semibold">评测任务未完成</strong>
            <span className="mt-1 block leading-5">{task.error_message}</span>
          </div>
        </div>
      ) : null}

      {task.request.evaluation_type !== "agent" && task.nodes && task.nodes.length > 0 ? (
        <EvaluationNodeInspector
          taskId={task.id}
          taskStatus={task.status}
          nodes={task.nodes}
          retryingNodeId={retryingNodeId}
          onRetry={onRetryNode}
        />
      ) : null}

      {task.result ? <EvaluationResultDetail result={task.result} /> : null}
    </div>
  );
}

/**
 * 按后端“最新任务在前”的列表顺序计算 FIFO 队列中仍处于活动态的更早任务数。
 *
 * @param tasks 后端按创建时间倒序返回的任务摘要。
 * @param taskIndex 当前任务在摘要列表中的索引；未选中时为负数。
 * @returns 当前任务之前需要等待的运行中或排队中任务数量。
 */
function countActiveTasksAhead(tasks: EvaluationTaskSummary[], taskIndex: number): number {
  if (taskIndex < 0) return 0;
  return tasks
    .slice(taskIndex + 1)
    .filter((task) => task.status === "pending" || task.status === "running").length;
}

interface TaskTrackProps {
  task: EvaluationTaskSummary;
  compact?: boolean;
}

/**
 * 把后端进度转换为带无障碍数值的可视进度轨道。
 *
 * @param props 任务进度摘要及是否使用列表紧凑尺寸。
 * @returns 约束在 0–100 范围内的进度条，运行态额外显示游标。
 */
function TaskTrack({ task, compact = false }: TaskTrackProps): JSX.Element {
  const percent = Math.min(100, Math.max(0, task.progress.percent));
  const running = task.status === "running";
  return (
    <div className="flex items-center gap-3">
      <div
        role="progressbar"
        aria-label={
          compact ? `任务 ${task.id} 进度 ${formatProgress(percent)}` : `任务进度 ${formatProgress(percent)}`
        }
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className={`task-track relative min-w-0 flex-1 overflow-visible rounded-sm bg-slate-200 ${compact ? "h-1.5" : "h-2"}`}
      >
        <span className="task-track-fill block h-full rounded-sm bg-primary" style={{ width: `${percent}%` }} />
        {running ? (
          <span
            className="task-track-cursor absolute top-1/2 h-3 w-1.5 rounded-[1px] bg-blue-700"
            style={{ left: `calc(${percent}% - 3px)` }}
            aria-hidden="true"
          />
        ) : null}
      </div>
      <span className="w-10 text-right font-mono text-[11px] font-semibold tabular-nums text-muted">
        {formatProgress(percent)}
      </span>
    </div>
  );
}

interface TelemetryCardProps {
  icon: typeof Activity;
  label: string;
  value: string;
  meta: string;
}

/**
 * 以统一布局呈现一个任务级遥测指标及其峰值或能力说明。
 *
 * @param props 图标、指标名称、主值和补充信息。
 * @returns 适配任务资源网格的单个指标卡片。
 */
function TelemetryCard({ icon: Icon, label, value, meta }: TelemetryCardProps): JSX.Element {
  return (
    <div className="min-w-0 border-b border-border px-5 py-4 last:border-b-0 sm:nth-[2n]:border-l lg:border-b-0 lg:border-l lg:first:border-l-0 sm:px-6">
      <span className="flex items-center gap-2 text-xs font-medium text-muted">
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        {label}
      </span>
      <strong className="mt-2 block truncate font-mono text-sm font-semibold tracking-tight text-ink">{value}</strong>
      <span className="mt-1 block truncate text-[11px] text-slate-400">{meta}</span>
    </div>
  );
}

/**
 * 将进度百分比格式化为紧凑标签，整数不保留无意义小数。
 *
 * @param percent 已经归一化的百分比数值。
 * @returns 带百分号的展示文本。
 */
function formatProgress(percent: number): string {
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`;
}

/**
 * 将任务耗时秒数转换为等宽的时钟格式，并把负数安全归零。
 *
 * @param seconds 后端返回的任务生命周期秒数。
 * @returns 小于一小时为 `MM:SS`，否则为 `HH:MM:SS`。
 */
function formatDuration(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainingSeconds = wholeSeconds % 60;
  const short = `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
  return hours > 0 ? `${hours.toString().padStart(2, "0")}:${short}` : short;
}

/**
 * 格式化可选资源百分比，不支持或尚无采样值时展示占位符。
 *
 * @param value CPU 或 GPU 当前/峰值百分比。
 * @returns 适合指标卡片的百分比文本。
 */
function formatPercent(value: number | null): string {
  if (value === null) return "—";
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

/**
 * 使用最接近的二进制容量单位格式化内存或显存字节数。
 *
 * @param value 资源采样字节数；`null` 表示当前平台不提供该指标。
 * @returns B、KB、MB 或 GB 展示文本。
 */
function formatBytes(value: number | null): string {
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  if (value < 1024 * 1024 * 1024) return `${Math.round(value / (1024 * 1024))} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
