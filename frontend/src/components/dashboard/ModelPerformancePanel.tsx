import { ArrowRight, Award, ChartNoAxesCombined, Medal, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type {
  ModelPerformanceModel,
  ModelPerformancePoint,
  ModelPerformanceResponse,
} from "../../types";
import { cn } from "../../lib/utils";
import { Panel } from "../ui/Panel";

interface ModelPerformancePanelProps {
  report: ModelPerformanceResponse | null;
  loading: boolean;
  error: string | null;
  onScopeChange: (scope: string) => void;
  onStartEvaluation: () => void;
}

const CHART_WIDTH = 680;
const CHART_HEIGHT = 250;
const CHART_LEFT = 48;
const CHART_TOP = 20;
const CHART_RIGHT = 18;
const CHART_BOTTOM = 38;

/** 把 0 到 1 的评测得分转换为便于比较的百分数。 */
function formatPercent(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}

/** 把 UTC 完成时间压缩为当前浏览器时区的月日和时分。 */
function formatCompletedAt(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** 将单次历史成绩映射到固定 SVG 画布，保证不同模型共享一致的 0–100% 纵轴。 */
function chartPoint(point: ModelPerformancePoint, index: number, total: number) {
  const innerWidth = CHART_WIDTH - CHART_LEFT - CHART_RIGHT;
  const innerHeight = CHART_HEIGHT - CHART_TOP - CHART_BOTTOM;
  const x =
    total === 1
      ? CHART_LEFT + innerWidth / 2
      : CHART_LEFT + (index / (total - 1)) * innerWidth;
  const score = Math.min(1, Math.max(0, point.score));
  return { x, y: CHART_TOP + (1 - score) * innerHeight };
}

/** 绘制选中模型的时间序列，并用绿色节点标记真实刷新历史最佳的运行。 */
function ScoreTrend({ model }: { model: ModelPerformanceModel }) {
  const coordinates = model.history.map((point, index) =>
    chartPoint(point, index, model.history.length),
  );
  const line = coordinates.map(({ x, y }) => `${x},${y}`).join(" ");
  const gridScores = [1, 0.75, 0.5, 0.25, 0];

  return (
    <div>
      <div className="overflow-x-auto">
        <svg
          role="img"
          aria-label={`${model.model} 历史成绩趋势`}
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          className="min-w-[560px]"
        >
          {gridScores.map((score) => {
            const y = CHART_TOP + (1 - score) * (CHART_HEIGHT - CHART_TOP - CHART_BOTTOM);
            return (
              <g key={score}>
                <line
                  x1={CHART_LEFT}
                  x2={CHART_WIDTH - CHART_RIGHT}
                  y1={y}
                  y2={y}
                  stroke="#e2e8f0"
                />
                <text
                  x={CHART_LEFT - 9}
                  y={y + 4}
                  textAnchor="end"
                  className="fill-slate-400 text-[10px]"
                >
                  {Math.round(score * 100)}
                </text>
              </g>
            );
          })}
          <polyline
            points={line}
            fill="none"
            stroke="#2563eb"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {model.history.map((point, index) => {
            const coordinate = chartPoint(point, index, model.history.length);
            return (
              <g key={point.task_id}>
                <circle
                  cx={coordinate.x}
                  cy={coordinate.y}
                  r={point.is_record ? 6 : 4.5}
                  fill={point.is_record ? "#16a34a" : "#2563eb"}
                  stroke="white"
                  strokeWidth="2"
                >
                  <title>{`${formatCompletedAt(point.completed_at)} · ${formatPercent(point.score)}${point.is_record ? " · 刷新纪录" : ""}`}</title>
                </circle>
                {(index === 0 || index === model.history.length - 1) && (
                  <text
                    x={coordinate.x}
                    y={CHART_HEIGHT - 13}
                    textAnchor={index === 0 ? "start" : "end"}
                    className="fill-slate-400 text-[10px]"
                  >
                    {formatCompletedAt(point.completed_at)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <ul className="sr-only">
        {model.history.map((point) => (
          <li key={point.task_id}>
            成绩点 {formatCompletedAt(point.completed_at)}，{formatPercent(point.score)}
            {point.is_record ? "，刷新最好成绩" : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 展示同一评测范围内的最佳成绩排行，并允许选择趋势图对应模型。 */
function ModelLeaderboard({
  models,
  selectedModel,
  onSelect,
}: {
  models: ModelPerformanceModel[];
  selectedModel: string;
  onSelect: (model: string) => void;
}) {
  return (
    <section aria-label="模型排行榜" className="space-y-2">
      {models.map((model, index) => {
        const selected = model.model === selectedModel;
        const gap = Math.max(0, (models[0]?.best_score || model.best_score) - model.best_score);
        return (
          <button
            key={model.model}
            type="button"
            aria-label={`第 ${index + 1} 名 ${model.model}，最佳 ${formatPercent(model.best_score)}`}
            aria-pressed={selected}
            onClick={() => onSelect(model.model)}
            className={cn(
              "group w-full rounded-lg border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
              selected ? "border-blue-300 bg-blue-50/70" : "border-border bg-white hover:border-slate-300",
            )}
          >
            <div className="flex items-center gap-3">
              <span
                className={cn(
                  "grid h-8 w-8 shrink-0 place-items-center rounded-md text-xs font-bold",
                  index === 0 ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500",
                )}
              >
                {index === 0 ? <Medal className="h-4 w-4" aria-hidden="true" /> : index + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm font-semibold text-ink">{model.model}</span>
                  <span className="font-mono text-sm font-bold text-ink">{formatPercent(model.best_score)}</span>
                </span>
                <span className="mt-2 block h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <span
                    className={cn("block h-full rounded-full", index === 0 ? "bg-amber-400" : "bg-blue-500")}
                    style={{ width: `${Math.min(100, Math.max(0, model.best_score * 100))}%` }}
                  />
                </span>
                <span className="mt-1.5 block text-[11px] text-muted">
                  最近 {formatPercent(model.latest_score)} · {model.run_count} 次评测
                  {gap > 0 ? ` · 距榜首 ${formatPercent(gap)}` : ""}
                </span>
              </span>
            </div>
          </button>
        );
      })}
    </section>
  );
}

/**
 * 呈现按 Benchmark 或 Suite 隔离的模型历史成绩、排行和纪录趋势。
 *
 * @param report 服务端已聚合的比较范围与模型表现。
 * @param loading 当前是否正在切换或刷新比较范围。
 * @param error 无法读取历史成绩时的可诊断消息。
 * @param onScopeChange 用户选择新的同口径比较范围时触发的加载回调。
 * @param onStartEvaluation 空历史状态进入模型评测配置页的导航回调。
 */
export function ModelPerformancePanel({
  report,
  loading,
  error,
  onScopeChange,
  onStartEvaluation,
}: ModelPerformancePanelProps) {
  const [selectedModel, setSelectedModel] = useState("");
  const models = report?.models || [];

  useEffect(() => {
    setSelectedModel((current) => {
      if (models.some((model) => model.model === current)) return current;
      return models[0]?.model || "";
    });
  }, [models]);

  const selected = useMemo(
    () => models.find((model) => model.model === selectedModel) || models[0] || null,
    [models, selectedModel],
  );
  const totalRuns = models.reduce((total, model) => total + model.run_count, 0);
  const record = report?.record;
  const leader = models[0] || null;

  return (
    <Panel aria-labelledby="model-performance-title" className="overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-border px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div>
          <p className="mb-1 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">Model performance</p>
          <h2 id="model-performance-title" className="text-lg font-semibold tracking-tight text-ink">模型历史成绩</h2>
          <p className="mt-1 text-sm leading-6 text-muted">只在同一个 Benchmark 或 Suite 内比较，避免不同口径混排。</p>
        </div>
        <label className="min-w-56 text-xs font-semibold text-slate-600">
          比较范围
          <select
            value={report?.selected_scope?.key || ""}
            onChange={(event) => onScopeChange(event.target.value)}
            disabled={loading || !report?.scopes.length}
            className="mt-1.5 block w-full rounded-md border border-border bg-white px-3 py-2 text-sm font-medium text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50"
          >
            {!report?.scopes.length ? <option value="">暂无可比较范围</option> : null}
            {report?.scopes.map((scope) => (
              <option key={scope.key} value={scope.key}>
                {scope.kind === "suite" ? "Suite" : "Benchmark"} · {scope.label} ({scope.run_count})
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <div role="alert" className="m-5 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 sm:m-6">{error}</div>
      ) : null}

      {loading && !report ? (
        <div className="grid min-h-72 place-items-center text-sm text-muted">正在整理模型历史成绩…</div>
      ) : error && !report ? (
        <div className="grid min-h-52 place-items-center px-6 text-center text-sm text-muted">
          历史成绩尚未载入，请检查本地服务后重试。
        </div>
      ) : !report || models.length === 0 ? (
        <div className="grid min-h-72 place-items-center px-6 text-center">
          <div>
            <ChartNoAxesCombined className="mx-auto h-8 w-8 text-slate-300" aria-hidden="true" />
            <p className="mt-3 text-sm font-semibold text-ink">还没有可比较的模型成绩</p>
            <p className="mt-1 text-xs leading-5 text-muted">完成至少一次模型评测后，这里会按相同评测范围生成排行与趋势。</p>
            <button
              type="button"
              onClick={onStartEvaluation}
              className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              发起模型评测
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : (
        <div className={cn("transition-opacity", loading && "opacity-60")}>
          <div className="grid border-b border-border sm:grid-cols-3">
            <div className="border-b border-border px-5 py-4 sm:border-r sm:border-b-0 sm:px-6">
              <p className="text-[11px] font-medium text-muted">参与模型</p>
              <p className="mt-1 text-xl font-semibold text-ink">{models.length}</p>
            </div>
            <div className="border-b border-border px-5 py-4 sm:border-r sm:border-b-0 sm:px-6">
              <p className="text-[11px] font-medium text-muted">累计评测</p>
              <p className="mt-1 text-xl font-semibold text-ink">{totalRuns}</p>
            </div>
            <div className="px-5 py-4 sm:px-6">
              <p className="text-[11px] font-medium text-muted">当前领先</p>
              <p className="mt-1 truncate text-sm font-semibold text-ink">{leader?.model}</p>
              <p className="mt-0.5 font-mono text-xs text-primary">{leader ? formatPercent(leader.best_score) : "—"}</p>
            </div>
          </div>

          {record?.improvement ? (
            <div className="mx-5 mt-5 flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 sm:mx-6">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white text-emerald-600">
                <Sparkles className="h-4 w-4" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="text-xs font-semibold text-emerald-800">刷新纪录 +{formatPercent(record.improvement)}</p>
                <p className="mt-0.5 truncate text-xs text-emerald-700">{record.model} · {formatPercent(record.score)}</p>
              </div>
            </div>
          ) : null}

          <div className="grid gap-6 p-5 lg:grid-cols-[minmax(250px,0.72fr)_minmax(0,1.6fr)] sm:p-6">
            <div>
              <div className="mb-3 flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <Award className="h-4 w-4 text-amber-500" aria-hidden="true" />
                  最佳成绩排行
                </h3>
                <span className="text-[11px] text-muted">点击查看趋势</span>
              </div>
              <ModelLeaderboard models={models} selectedModel={selected?.model || ""} onSelect={setSelectedModel} />
            </div>

            {selected ? (
              <section aria-label="模型成绩趋势" className="min-w-0 rounded-lg border border-border bg-slate-50/50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium text-muted">历史趋势</p>
                    <h3 className="mt-1 text-sm font-semibold text-ink">{selected.model}</h3>
                    {models.length === 1 ? (
                      <p className="mt-1 text-[11px] text-muted">当前范围暂无其他模型可比较</p>
                    ) : null}
                    {selected.history.length === 1 ? (
                      <p className="mt-1 text-[11px] font-medium text-blue-700">首次成绩 · 等待后续趋势</p>
                    ) : null}
                  </div>
                  <div className="flex gap-4 text-right">
                    <div>
                      <p className="text-[10px] text-muted">最佳</p>
                      <p className="font-mono text-sm font-semibold text-ink">{formatPercent(selected.best_score)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted">最近</p>
                      <p className="font-mono text-sm font-semibold text-ink">{formatPercent(selected.latest_score)}</p>
                    </div>
                  </div>
                </div>
                <div className="mt-4">
                  <ScoreTrend model={selected} />
                </div>
                <div className="mt-2 flex items-center justify-end gap-4 text-[10px] text-muted">
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-blue-600" />普通成绩</span>
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-emerald-600" />刷新最好成绩</span>
                </div>
              </section>
            ) : null}
          </div>
        </div>
      )}
    </Panel>
  );
}
