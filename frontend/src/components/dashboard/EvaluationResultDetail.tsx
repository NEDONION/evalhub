import { Braces } from "lucide-react";
import type { JSX } from "react";

import { formatPassRate, formatScore } from "../../lib/evaluation";
import type { AgentDifficulty, EvaluationResult } from "../../types";
import { Badge } from "../ui/Badge";
import { AgentCapabilityHexagon } from "./AgentCapabilityHexagon";
import { CapabilityRadar } from "./CapabilityRadar";

interface EvaluationResultDetailProps {
  result: EvaluationResult;
  isHexagon?: boolean;
}

const difficultyLabels: Record<AgentDifficulty, string> = {
  all: "全部",
  easy: "简单",
  medium: "中等",
  hard: "困难",
};

/**
 * 在已成功任务内部展示聚合指标、失败样例和默认折叠的原始结果。
 *
 * @param props 后端持久化的完整评测结果。
 * @returns 只属于当前任务详情的结果区，避免列表页一次展开大量原始数据。
 */
export function EvaluationResultDetail({ result, isHexagon = false }: EvaluationResultDetailProps): JSX.Element {
  return (
    <div className="border-t border-border">
      <div className="flex items-start justify-between gap-3 px-5 py-5 sm:px-6">
        <div>
          <p className="mb-1 text-[10px] font-semibold tracking-[0.12em] text-primary uppercase">
            Evaluation output
          </p>
          <h3 className="text-sm font-semibold text-ink">评测结果</h3>
          <p className="mt-1 text-xs leading-5 text-muted">聚合指标与失败样例仅在当前任务详情中展示。</p>
        </div>
        <Badge tone={result.status === "partial" ? "warning" : "success"} dot>
          {result.status === "partial" ? "部分完成" : "已完成"}
        </Badge>
      </div>

      <div className="result-grid grid border-y border-border sm:grid-cols-2 lg:grid-cols-4">
        <ResultMetric label="Benchmark" value={result.benchmark} />
        <ResultMetric label="模型" value={result.model} mono />
        <ResultMetric
          label="通过样本"
          value={`${result.passed_samples} / ${result.total_samples}`}
          meta={formatPassRate(result.passed_samples, result.total_samples)}
        />
        <ResultMetric label="平均分" value={formatScore(result.average_score)} accent />
      </div>

      {result.capability_report ? (
        <>
          <AgentCapabilityHexagon report={result.capability_report} />
          {result.agent ? (
            <div className="grid border-b border-border bg-slate-50/55 px-5 py-3 text-xs text-muted sm:grid-cols-3 sm:px-6">
              <span>
                Agent 壳 <strong className="ml-1 font-mono text-ink">{result.agent.framework}</strong>
              </span>
              <span>
                CLI <strong className="ml-1 font-mono text-ink">{result.agent.cli_version}</strong>
              </span>
              <span>
                脚手架 <strong className="ml-1 font-mono text-ink">{result.agent.scaffold_hash}</strong>
              </span>
            </div>
          ) : null}
        </>
      ) : null}

      {result.capability_profile ? <CapabilityRadar profile={result.capability_profile} /> : null}

      {result.reproducibility ? <ReproducibilityLedger reproducibility={result.reproducibility} /> : null}

      {result.difficulty_report && result.difficulty_report.length > 0 ? (
        <section aria-labelledby="difficulty-report-title" className="border-b border-border px-5 py-5 sm:px-6">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <h4 id="difficulty-report-title" className="text-sm font-semibold text-ink">
              难度分层
            </h4>
            <span className="text-xs text-muted">
              题集 {result.benchmark_version || "—"} · 请求 {difficultyLabels[result.requested_difficulty || "all"]}
            </span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {result.difficulty_report.map((tier) => (
              <article key={tier.difficulty} className="rounded-md border border-border bg-slate-50/60 px-3 py-3">
                <span className="text-xs font-medium text-muted">{difficultyLabels[tier.difficulty]}</span>
                <strong className="mt-1 block text-sm font-semibold text-ink">
                  {tier.passed} / {tier.total}
                </strong>
                <span className="text-xs text-primary">{formatPassRate(tier.passed, tier.total)}</span>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {result.failed_examples.length > 0 ? (
        <div className="border-b border-border px-5 py-5 sm:px-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="text-sm font-semibold text-ink">失败样例</h4>
            <span className="text-xs text-muted">最多展示 5 条</span>
          </div>
          <div className="grid gap-2 lg:grid-cols-2">
            {result.failed_examples.map((example) => (
              <article key={example.sample_id} className="min-w-0 rounded-md border border-border bg-slate-50/60 p-3">
                <div className="flex items-center justify-between gap-3">
                  <code className="truncate font-mono text-[11px] text-slate-500">{example.sample_id}</code>
                  <Badge tone="danger">得分 {formatScore(example.score)}</Badge>
                </div>
                {example.difficulty ? (
                  <p className="mt-2 text-xs text-slate-500">
                    {difficultyLabels[example.difficulty]} · {example.difficulty_reason}
                  </p>
                ) : null}
                <p className="mt-3 line-clamp-2 text-xs leading-5 text-muted">{example.input}</p>
                <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  <span className="rounded border border-red-100 bg-red-50 px-2 py-1.5 text-red-700">
                    预测：{example.prediction}
                  </span>
                  <span className="rounded border border-emerald-100 bg-emerald-50 px-2 py-1.5 text-emerald-700">
                    参考：{example.reference}
                  </span>
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : null}

      {!isHexagonResult(result, isHexagon) ? (
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
      ) : null}

    </div>
  );
}

function ReproducibilityLedger({
  reproducibility,
}: {
  reproducibility: NonNullable<EvaluationResult["reproducibility"]>;
}): JSX.Element {
  const sourceRevisions = Object.entries(reproducibility.source_revisions || {});
  const promptTemplates = Object.entries(reproducibility.prompt_template_versions || {});
  const promptConfig = Object.entries(reproducibility.generation_config || {});

  return (
    <details className="border-b border-border">
      <summary className="cursor-pointer px-5 py-3 text-xs font-medium text-muted hover:bg-slate-50 sm:px-6">
        可复现性账本
      </summary>
      <dl className="grid gap-x-5 gap-y-2 border-t border-border px-5 py-4 text-xs sm:grid-cols-2 sm:px-6">
        <LedgerRow label="套件版本" value={reproducibility.suite_version} />
        <LedgerRow label="清单摘要" value={reproducibility.manifest_sha256} mono />
        <LedgerRow label="来源版本" value={sourceRevisions.map(([key, value]) => `${key}: ${value}`).join(" · ")} />
        <LedgerRow label="提示模板" value={promptTemplates.map(([key, value]) => `${key}: ${value}`).join(" · ")} />
        <LedgerRow label="生成配置" value={promptConfig.map(([key, value]) => `${key}: ${value}`).join(" · ")} />
      </dl>
    </details>
  );
}

function isHexagonResult(result: EvaluationResult, isHexagon: boolean): boolean {
  return (
    isHexagon ||
    result.suite_id === "evalhub-hexagon-v1" ||
    result.dataset.startsWith("hexagon-") ||
    result.benchmark.includes("六边形")
  );
}

function LedgerRow({ label, value, mono = false }: { label: string; value?: string; mono?: boolean }): JSX.Element {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className={`${mono ? "font-mono" : ""} mt-1 break-all text-ink`}>{value || "—"}</dd>
    </div>
  );
}

interface ResultMetricProps {
  label: string;
  value: string;
  meta?: string;
  mono?: boolean;
  accent?: boolean;
}

/**
 * 渲染结果摘要网格中的单个指标，并按需要应用等宽或强调样式。
 *
 * @param props 指标标题、主值、补充文本和显示风格。
 * @returns 结果网格中的一个语义化指标单元。
 */
function ResultMetric({
  label,
  value,
  meta,
  mono = false,
  accent = false,
}: ResultMetricProps): JSX.Element {
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
