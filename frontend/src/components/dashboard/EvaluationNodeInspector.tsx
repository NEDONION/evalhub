import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleDashed,
  Clock3,
  RotateCcw,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";

import { getEvaluationNode, getEvaluationNodeSamples } from "../../lib/api";
import type {
  EvaluationNodeDetail,
  EvaluationNodeStatus,
  EvaluationNodeSummary,
  EvaluationSampleCheckpoint,
  EvaluationTaskStatus,
} from "../../types";
import { Badge, type BadgeProps } from "../ui/Badge";
import { Button } from "../ui/Button";

interface EvaluationNodeInspectorProps {
  taskId: string;
  taskStatus: EvaluationTaskStatus;
  nodes: EvaluationNodeSummary[];
  retryingNodeId: string | null;
  onRetry: (taskId: string, nodeId: string) => Promise<unknown> | void;
}

const statusCopy: Record<EvaluationNodeStatus, { label: string; tone: BadgeProps["tone"] }> = {
  pending: { label: "待执行", tone: "neutral" },
  running: { label: "运行中", tone: "info" },
  success: { label: "成功", tone: "success" },
  failed: { label: "失败", tone: "danger" },
  blocked: { label: "阻塞", tone: "warning" },
  canceled: { label: "已取消", tone: "warning" },
};

const kindLabels: Record<string, string> = {
  prepare_assets: "准备运行资产",
  benchmark: "执行 Benchmark",
  capability_aggregate: "聚合能力画像",
  workflow_finalize: "生成最终报告",
};

/** 展示持久化 DAG 节点、运行快照、审计事件和失败样本。 */
export function EvaluationNodeInspector({
  taskId,
  taskStatus,
  nodes,
  retryingNodeId,
  onRetry,
}: EvaluationNodeInspectorProps): JSX.Element {
  const preferredNodeId = useMemo(
    () =>
      nodes.find((node) => node.status === "running")?.id ||
      nodes.find((node) => node.status === "failed" || node.status === "blocked")?.id ||
      nodes[0]?.id ||
      null,
    [nodes],
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(preferredNodeId);
  const [detail, setDetail] = useState<EvaluationNodeDetail | null>(null);
  const [samples, setSamples] = useState<EvaluationSampleCheckpoint[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedNodeId((current) => (current && nodes.some((node) => node.id === current) ? current : preferredNodeId));
  }, [nodes, preferredNodeId]);

  useEffect(() => {
    setDetail(null);
    setSamples([]);
    setNextCursor(null);
    setError(null);
  }, [taskId, selectedNodeId]);

  useEffect(() => {
    if (!selectedNodeId) return;
    let active = true;
    setLoading(true);
    setError(null);
    void Promise.all([
      getEvaluationNode(taskId, selectedNodeId),
      getEvaluationNodeSamples(taskId, selectedNodeId, { status: "failed", limit: 20 }),
    ])
      .then(([nextDetail, samplePage]) => {
        if (!active) return;
        setDetail(nextDetail);
        setSamples(samplePage.samples);
        setNextCursor(samplePage.next_cursor);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "节点详情读取失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [taskId, selectedNodeId, nodeRevision(nodes)]);

  async function loadMoreFailures(): Promise<void> {
    if (!selectedNodeId || !nextCursor || loading) return;
    setLoading(true);
    try {
      const page = await getEvaluationNodeSamples(taskId, selectedNodeId, {
        status: "failed",
        limit: 20,
        cursor: nextCursor,
      });
      setSamples((current) => [...current, ...page.samples]);
      setNextCursor(page.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "失败样本读取失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section aria-labelledby="workflow-nodes-title" className="border-t border-border">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 sm:px-6">
        <div>
          <p className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.12em] text-primary uppercase">
            <Workflow className="h-3.5 w-3.5" aria-hidden="true" />
            Runtime workflow
          </p>
          <h3 id="workflow-nodes-title" className="mt-1 text-sm font-semibold text-ink">
            执行节点与审计
          </h3>
        </div>
        <span className="font-mono text-[11px] text-slate-400">{nodes.length} NODES</span>
      </div>

      <div className="grid border-t border-border lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="divide-y divide-border border-b border-border lg:border-r lg:border-b-0">
          {nodes.map((node, index) => (
            <button
              key={node.id}
              type="button"
              onClick={() => setSelectedNodeId(node.id)}
              aria-current={selectedNodeId === node.id ? "step" : undefined}
              className={`grid w-full grid-cols-[24px_minmax(0,1fr)] gap-3 px-4 py-3 text-left transition-colors ${selectedNodeId === node.id ? "bg-blue-50/70" : "hover:bg-slate-50"}`}
            >
              <span className="relative mt-0.5 flex h-6 w-6 items-center justify-center">
                {node.status === "success" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                ) : node.status === "running" ? (
                  <CircleDashed className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
                ) : node.status === "failed" || node.status === "blocked" ? (
                  <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />
                ) : (
                  <Circle className="h-3.5 w-3.5 text-slate-300" aria-hidden="true" />
                )}
                {index < nodes.length - 1 ? (
                  <span className="absolute top-5 h-5 w-px bg-slate-200" aria-hidden="true" />
                ) : null}
              </span>
              <span className="min-w-0">
                <span className="flex items-center justify-between gap-2">
                  <strong className="truncate text-xs font-semibold text-ink">
                    {nodeLabel(node)}
                  </strong>
                  <span className="font-mono text-[10px] tabular-nums text-slate-400">
                    {formatMilliseconds(node.timing.elapsed_ms)}
                  </span>
                </span>
                <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted">
                  <span>{statusCopy[node.status].label}</span>
                  <span className="font-mono">尝试 {node.attempt.count}/{node.attempt.max}</span>
                </span>
              </span>
            </button>
          ))}
        </div>

        <div className="min-w-0">
          {loading && !detail ? (
            <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-muted">
              <CircleDashed className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
              正在读取节点快照
            </div>
          ) : error ? (
            <div role="alert" className="flex items-start gap-2 bg-red-50 px-5 py-4 text-sm text-red-700 sm:px-6">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {error}
            </div>
          ) : detail && detail.id === selectedNodeId ? (
            <NodeDetail
              detail={detail}
              samples={samples}
              hasMoreSamples={Boolean(nextCursor)}
              loading={loading}
              canRetry={
                taskStatus === "failed" &&
                (detail.status === "failed" || detail.status === "blocked")
              }
              retrying={retryingNodeId === detail.id}
              onRetry={() => onRetry(taskId, detail.id)}
              onLoadMore={() => void loadMoreFailures()}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}

interface NodeDetailProps {
  detail: EvaluationNodeDetail;
  samples: EvaluationSampleCheckpoint[];
  hasMoreSamples: boolean;
  loading: boolean;
  canRetry: boolean;
  retrying: boolean;
  onRetry: () => Promise<unknown> | void;
  onLoadMore: () => void;
}

function NodeDetail({
  detail,
  samples,
  hasMoreSamples,
  loading,
  canRetry,
  retrying,
  onRetry,
  onLoadMore,
}: NodeDetailProps): JSX.Element {
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 sm:px-6">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={statusCopy[detail.status].tone} dot={detail.status === "running"}>
              {statusCopy[detail.status].label}
            </Badge>
            <span className="flex items-center gap-1 font-mono text-[11px] text-muted">
              <Clock3 className="h-3 w-3" aria-hidden="true" />
              {formatMilliseconds(detail.timing.elapsed_ms)}
            </span>
          </div>
          <code className="mt-2 block truncate font-mono text-xs text-slate-500">{detail.node_key}</code>
        </div>
        {canRetry ? (
          <Button variant="secondary" size="sm" disabled={retrying} onClick={() => void onRetry()}>
            <RotateCcw className={`h-3.5 w-3.5 ${retrying ? "animate-spin" : ""}`} aria-hidden="true" />
            {retrying ? "正在重试" : "重试此节点"}
          </Button>
        ) : null}
      </div>

      {detail.error ? (
        <div className="border-t border-amber-100 bg-amber-50 px-5 py-3 text-xs leading-5 text-amber-800 sm:px-6">
          <strong className="font-mono">{detail.error.type || "runtime_error"}</strong>
          <span className="ml-2">{detail.error.message}</span>
        </div>
      ) : null}

      <details className="border-t border-border">
        <summary className="cursor-pointer px-5 py-3 text-xs font-medium text-muted hover:bg-slate-50 sm:px-6">
          输入、检查点与输出
        </summary>
        <pre className="max-h-72 overflow-auto border-t border-slate-800 bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-200">
          {JSON.stringify({ input: detail.input, checkpoint: detail.checkpoint, output: detail.output }, null, 2)}
        </pre>
      </details>

      <div className="border-t border-border">
        <div className="flex items-center justify-between gap-3 px-5 py-3 sm:px-6">
          <h4 className="text-xs font-semibold text-ink">审计事件</h4>
          <span className="font-mono text-[10px] text-slate-400">{detail.events.length} EVENTS</span>
        </div>
        <div className="max-h-64 divide-y divide-border overflow-auto border-t border-border">
          {detail.events.map((event) => (
            <div key={event.id} className="grid gap-1 px-5 py-3 text-xs sm:grid-cols-[150px_100px_minmax(0,1fr)] sm:px-6">
              <time className="font-mono text-[10px] text-slate-400">{formatTimestamp(event.created_at)}</time>
              <strong className="font-mono text-[10px] text-ink">{event.event_type}</strong>
              <span className="min-w-0 truncate text-muted">
                {event.message || `${event.from_status || "—"} → ${event.to_status || "—"}`} · {event.actor}
              </span>
            </div>
          ))}
        </div>
      </div>

      {samples.length > 0 ? (
        <div className="border-t border-border">
          <div className="flex items-center justify-between gap-3 px-5 py-3 sm:px-6">
            <h4 className="text-xs font-semibold text-ink">失败样本</h4>
            <span className="text-[11px] text-muted">{samples.length} 条</span>
          </div>
          <div className="max-h-72 divide-y divide-border overflow-auto border-t border-border">
            {samples.map((sample) => (
              <div key={sample.sample_key} className="px-5 py-3 sm:px-6">
                <div className="flex items-center justify-between gap-3">
                  <code className="truncate font-mono text-[11px] text-slate-500">{sample.sample_key}</code>
                  <span className="font-mono text-[10px] text-red-600">ATTEMPT {sample.attempt_count}</span>
                </div>
                <p className="mt-1 truncate text-xs text-muted">
                  {sampleErrorMessage(sample)}
                </p>
              </div>
            ))}
          </div>
          {hasMoreSamples ? (
            <div className="border-t border-border px-5 py-3 text-right sm:px-6">
              <Button variant="ghost" size="sm" disabled={loading} onClick={onLoadMore}>
                加载更多
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function nodeRevision(nodes: EvaluationNodeSummary[]): string {
  return nodes
    .map((node) => `${node.id}:${node.status}:${node.attempt.count}:${node.progress.completed_samples}`)
    .join("|");
}

function nodeLabel(node: EvaluationNodeSummary): string {
  if (node.kind === "benchmark") {
    return String(node.node_key.split(":").slice(1).join(":")) || "Benchmark";
  }
  return kindLabels[node.kind] || node.kind;
}

function formatMilliseconds(value: number): string {
  if (value < 1000) return `${value} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(1)} s`;
  return `${Math.floor(value / 60_000)}m ${Math.floor((value % 60_000) / 1000)}s`;
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function sampleErrorMessage(sample: EvaluationSampleCheckpoint): string {
  const message = sample.last_error?.message;
  if (typeof message === "string") return message;
  return JSON.stringify(sample.last_error || sample.result || {});
}
