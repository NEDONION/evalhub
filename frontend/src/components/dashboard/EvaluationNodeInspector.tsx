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
import { sampleMetadata } from "../../lib/assets";
import { formatScore } from "../../lib/evaluation";
import type {
  EvaluationNodeDetail,
  EvaluationNodeEvent,
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
  agent_benchmark: "执行 Agent Benchmark",
  capability_aggregate: "聚合能力画像",
  workflow_finalize: "生成最终报告",
};

/** 展示持久化 DAG 节点、运行快照、审计事件和可审计样本。 */
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
      getEvaluationNodeSamples(taskId, selectedNodeId, { limit: 20 }),
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

  async function loadMoreSamples(): Promise<void> {
    if (!selectedNodeId || !nextCursor || loading) return;
    setLoading(true);
    try {
      const page = await getEvaluationNodeSamples(taskId, selectedNodeId, {
        limit: 20,
        cursor: nextCursor,
      });
      setSamples((current) => [...current, ...page.samples]);
      setNextCursor(page.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "样本明细读取失败");
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
              onLoadMore={() => void loadMoreSamples()}
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

/**
 * 展示单个节点的运行快照，并为 Agent 节点切换到可读的实时过程时间线。
 *
 * @param props 节点详情、样本明细、重试和分页操作。
 * @returns 包含诊断、原始检查点、事件与可读样本证据的节点详情区域。
 */
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

      <NodeEventTimeline detail={detail} />

      {samples.length > 0 ? (
        <div className="border-t border-border">
          <div className="flex items-center justify-between gap-3 px-5 py-3 sm:px-6">
            <h4 className="text-xs font-semibold text-ink">样本明细</h4>
            <span className="text-[11px] text-muted">{samples.length} 条</span>
          </div>
          <div className="max-h-[36rem] divide-y divide-border overflow-auto border-t border-border">
            {samples.map((sample) => (
              <SampleEvidence key={sample.sample_key} sample={sample} />
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

interface NodeEventTimelineProps {
  detail: EvaluationNodeDetail;
}

const agentEventLabels: Record<string, string> = {
  node_created: "节点已创建",
  node_started: "节点已启动",
  agent_session_started: "Agent 会话已建立",
  agent_turn_started: "Agent 开始处理",
  sample_started: "开始样本",
  tool_started: "调用工具",
  tool_finished: "工具完成",
  agent_message: "Agent 消息",
  workspace_changed: "工作区变化",
  verifier_finished: "隐藏校验",
  sample_finished: "样本完成",
  runner_error: "运行异常",
  node_succeeded: "节点成功",
  node_failed: "节点失败",
  node_canceled: "节点取消",
};

/**
 * 将节点事件展示为可追踪时间线；Agent 节点会展开命令输出等白名单证据。
 *
 * @param props 当前节点详情，其中事件已由后端按创建顺序返回。
 * @returns 通用审计列表或 Agent 实时过程日志；运行态通过 aria-live 通知增量变化。
 */
function NodeEventTimeline({ detail }: NodeEventTimelineProps): JSX.Element {
  const isAgent = detail.kind === "agent_benchmark";
  return (
    <div className="border-t border-border">
      <div className="flex items-center justify-between gap-3 px-5 py-3 sm:px-6">
        <h4 className="text-xs font-semibold text-ink">
          {isAgent ? "Agent 实时过程" : "审计事件"}
        </h4>
        <span className="font-mono text-[10px] text-slate-400">
          {detail.events.length} EVENTS
        </span>
      </div>
      <div
        role={isAgent ? "log" : undefined}
        aria-live={isAgent && detail.status === "running" ? "polite" : undefined}
        className="max-h-80 divide-y divide-border overflow-auto border-t border-border"
      >
        {detail.events.length === 0 ? (
          <p className="px-5 py-4 text-xs text-muted sm:px-6">
            {isAgent ? "等待 Agent 产生可观察事件" : "暂无审计事件"}
          </p>
        ) : (
          detail.events.map((event) => {
            const evidence = isAgent ? agentEventEvidence(event) : null;
            return (
              <div
                key={event.id}
                className="grid gap-2 px-5 py-3 text-xs sm:grid-cols-[100px_minmax(0,1fr)] sm:px-6"
              >
                <div>
                  <time className="block font-mono text-[10px] text-slate-400">
                    {formatTimestamp(event.created_at)}
                  </time>
                  <span className="mt-1 block font-mono text-[10px] text-slate-400">
                    {event.actor}
                  </span>
                </div>
                <div className="min-w-0">
                  <strong className="font-mono text-[10px] text-ink">
                    {isAgent ? agentEventLabels[event.event_type] || event.event_type : event.event_type}
                  </strong>
                  <p className="mt-1 whitespace-pre-wrap text-muted">
                    {event.message || `${event.from_status || "—"} → ${event.to_status || "—"}`}
                  </p>
                  {evidence ? (
                    <pre className="mt-2 max-h-32 overflow-auto rounded bg-slate-950 px-3 py-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-slate-200">
                      {evidence}
                    </pre>
                  ) : null}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

/**
 * 从后端白名单载荷提取最有诊断价值的证据，避免把完整任意 JSON 倾倒到界面。
 *
 * @param event 单条已持久化节点事件。
 * @returns 工具输出、修改文件或校验摘要；没有额外证据时返回空值。
 */
function agentEventEvidence(event: EvaluationNodeEvent): string | null {
  const output = event.payload?.output;
  if (typeof output === "string" && output) return output;
  const changedFiles = event.payload?.changed_files;
  if (Array.isArray(changedFiles)) {
    const files = changedFiles.filter((item): item is string => typeof item === "string");
    return files.length > 0 ? files.join("\n") : null;
  }
  const verifierMessage =
    event.event_type === "verifier_finished" ? event.payload?.message : null;
  if (typeof verifierMessage === "string" && verifierMessage) return verifierMessage;
  return null;
}

/**
 * 生成节点摘要变化指纹；运行耗时每秒变化时也会触发详情重新读取，从而刷新 Trace。
 *
 * @param nodes 选中任务最新的节点摘要列表。
 * @returns 用于 Effect 依赖比较的稳定字符串。
 */
function nodeRevision(nodes: EvaluationNodeSummary[]): string {
  return nodes
    .map(
      (node) =>
        `${node.id}:${node.status}:${node.attempt.count}:${node.progress.completed_samples}:${node.timing.elapsed_ms}`,
    )
    .join("|");
}

/**
 * 把节点类型和稳定键转换为面向用户的短标签。
 *
 * @param node 一个工作流节点摘要。
 * @returns Benchmark 标识、Agent Benchmark 名称或已知节点种类文案。
 */
function nodeLabel(node: EvaluationNodeSummary): string {
  if (node.kind === "benchmark" || node.kind === "agent_benchmark") {
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

function SampleEvidence({ sample }: { sample: EvaluationSampleCheckpoint }): JSX.Element {
  const metadata = sampleMetadata(sample.result?.metadata);
  const input = typeof sample.input.input === "string" ? sample.input.input : "—";
  const prediction = typeof sample.result?.prediction === "string" ? sample.result.prediction : null;
  const score = typeof sample.result?.score === "number" ? formatScore(sample.result.score) : "—";
  const source = metadata?.source || "—";
  const sourceKey = metadata?.source_key || sample.sample_key;

  return (
    <article className="px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-medium text-ink">{sample.status === "success" ? "成功" : "失败"}</span>
        <span className="text-muted">得分 {score}</span>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-ink">{input}</p>
      {prediction ? (
        <div className="mt-3 border-l-2 border-slate-200 bg-slate-50/70 px-3 py-2.5 text-xs leading-5 text-slate-700">
          <strong className="block font-medium text-ink">模型回答</strong>
          <p className="mt-1 whitespace-pre-wrap">{prediction}</p>
        </div>
      ) : null}
      {metadata?.input_zh ? (
        <div className="mt-3 border-l-2 border-blue-200 bg-blue-50/60 px-3 py-2.5 text-xs leading-5 text-slate-700">
          <strong className="block font-medium text-ink">中文译文</strong>
          <p className="mt-1 whitespace-pre-wrap">{metadata.input_zh}</p>
          <span className="mt-1 block text-[10px] text-muted">EvalHub 中文辅助翻译，非官方译文</span>
        </div>
      ) : null}
      <div className="mt-3 grid gap-1 text-[11px] text-muted sm:grid-cols-2">
        <span>来源 {source}</span>
        <span>来源键 <code className="font-mono text-slate-600">{sourceKey}</code></span>
      </div>
    </article>
  );
}
