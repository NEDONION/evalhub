import { AlertTriangle, Braces, ChartNoAxesColumnIncreasing, CheckCircle2, CircleDashed } from "lucide-react";

import { formatPassRate, formatScore } from "../../lib/evaluation";
import type { EvaluationResult } from "../../types";
import { Badge } from "../ui/Badge";
import { Panel } from "../ui/Panel";

interface ResultPanelProps {
  result: EvaluationResult | null;
  running: boolean;
  error: string | null;
}

const statusLabels: Record<string, string> = {
  pending: "等待中",
  running: "运行中",
  success: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export function ResultPanel({ result, running, error }: ResultPanelProps) {
  return (
    <Panel aria-labelledby="result-title" className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-border px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div>
          <p className="mb-1 flex items-center gap-2 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">
            <ChartNoAxesColumnIncreasing className="h-3.5 w-3.5" aria-hidden="true" />
            Evaluation output
          </p>
          <h2 id="result-title" className="text-base font-semibold tracking-tight text-ink">
            评测结果
          </h2>
          <p className="mt-1 text-sm text-muted">先阅读聚合指标，需要排查时再展开原始结果。</p>
        </div>
        {running ? (
          <Badge tone="info" dot>
            正在评测
          </Badge>
        ) : result ? (
          <Badge tone={result.status === "success" ? "success" : "warning"} dot>
            {statusLabels[result.status] || result.status}
          </Badge>
        ) : (
          <Badge>等待结果</Badge>
        )}
      </div>

      <div aria-live="polite">
        {error ? (
          <div role="alert" className="flex items-start gap-3 border-b border-red-100 bg-red-50 px-5 py-4 text-sm text-red-700 sm:px-6">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <div>
              <strong className="block font-semibold">评测没有完成</strong>
              <span className="mt-1 block leading-5">{error}</span>
            </div>
          </div>
        ) : null}

        {running && !result ? (
          <div className="grid min-h-48 place-items-center px-5 py-10 text-center sm:px-6">
            <div>
              <CircleDashed className="mx-auto h-8 w-8 animate-spin text-primary" aria-hidden="true" />
              <strong className="mt-4 block text-sm font-semibold text-ink">正在执行评测</strong>
              <p className="mt-1 text-sm text-muted">模型响应完成后，这里会显示得分和失败样例。</p>
            </div>
          </div>
        ) : null}

        {!running && !result ? (
          <div className="grid min-h-48 place-items-center px-5 py-10 text-center sm:px-6">
            <div>
              <div className="relative mx-auto grid h-12 w-12 place-items-center rounded-lg border border-blue-200 bg-blue-50 text-primary">
                <CheckCircle2 className="h-5 w-5" aria-hidden="true" />
                <span className="absolute -right-1 -bottom-1 h-3 w-3 rounded-full border-2 border-white bg-blue-500" aria-hidden="true" />
              </div>
              <strong className="mt-4 block text-sm font-semibold text-ink">尚未运行评测</strong>
              <p className="mt-1 text-sm text-muted">配置上方参数后发起第一次评测。</p>
            </div>
          </div>
        ) : null}

        {result ? (
          <>
            <div className="result-grid grid border-b border-border sm:grid-cols-2 lg:grid-cols-5">
              <ResultMetric label="任务状态" value={statusLabels[result.status] || result.status} />
              <ResultMetric label="Benchmark" value={result.benchmark} />
              <ResultMetric label="模型" value={result.model} mono />
              <ResultMetric
                label="通过样本"
                value={`${result.passed_samples} / ${result.total_samples}`}
                meta={formatPassRate(result.passed_samples, result.total_samples)}
              />
              <ResultMetric label="平均分" value={formatScore(result.average_score)} accent />
            </div>

            {result.failed_examples.length > 0 ? (
              <div className="border-b border-border px-5 py-5 sm:px-6">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-ink">失败样例</h3>
                  <span className="text-xs text-muted">最多展示 5 条</span>
                </div>
                <div className="grid gap-2 lg:grid-cols-2">
                  {result.failed_examples.map((example) => (
                    <article key={example.sample_id} className="min-w-0 rounded-md border border-border bg-slate-50/60 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <code className="truncate font-mono text-[11px] text-slate-500">{example.sample_id}</code>
                        <Badge tone="danger">得分 {formatScore(example.score)}</Badge>
                      </div>
                      <p className="mt-3 line-clamp-2 text-xs leading-5 text-muted">{example.input}</p>
                      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                        <span className="rounded border border-red-100 bg-red-50 px-2 py-1.5 text-red-700">预测：{example.prediction}</span>
                        <span className="rounded border border-emerald-100 bg-emerald-50 px-2 py-1.5 text-emerald-700">参考：{example.reference}</span>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            ) : null}

            <details className="group">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-3 text-xs font-medium text-muted transition-colors hover:bg-slate-50 hover:text-ink sm:px-6">
                <span className="flex items-center gap-2">
                  <Braces className="h-3.5 w-3.5" aria-hidden="true" />
                  原始 JSON
                </span>
                <span className="font-mono text-[10px] text-slate-400 group-open:hidden">EXPAND</span>
                <span className="hidden font-mono text-[10px] text-slate-400 group-open:inline">COLLAPSE</span>
              </summary>
              <pre className="max-h-[32rem] overflow-auto border-t border-slate-800 bg-slate-950 p-5 font-mono text-xs leading-5 text-slate-200 sm:p-6">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          </>
        ) : null}
      </div>
    </Panel>
  );
}

interface ResultMetricProps {
  label: string;
  value: string;
  meta?: string;
  mono?: boolean;
  accent?: boolean;
}

function ResultMetric({ label, value, meta, mono = false, accent = false }: ResultMetricProps) {
  return (
    <div className="min-w-0 border-b border-border px-5 py-4 last:border-b-0 sm:nth-[2n]:border-l lg:border-b-0 lg:border-l lg:first:border-l-0 sm:px-6">
      <span className="block text-xs font-medium text-muted">{label}</span>
      <strong
        className={`${mono ? "font-mono text-xs" : "text-base"} ${accent ? "text-primary" : "text-ink"} mt-2 block truncate font-semibold tracking-tight`}
        title={value}
      >
        {value}
      </strong>
      {meta ? <span className="mt-1 block text-xs font-medium text-blue-600">{meta}</span> : null}
    </div>
  );
}
