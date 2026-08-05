import { Braces } from "lucide-react";
import type { JSX } from "react";

import { formatPassRate, formatScore } from "../../lib/evaluation";
import type { AgentDifficulty, AgentSampleOutcome, EvaluationResult } from "../../types";
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

const outcomeLabels: Record<AgentSampleOutcome, string> = {
  passed: "通过",
  no_action: "无动作",
  wrong_solution: "错误实现",
  runtime_error: "运行失败",
  protocol_error: "协议错误",
};

const protocolLabels = {
  compatible: "协议兼容",
  degraded: "协议降级",
  incompatible: "协议不兼容",
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

      {result.execution_summary ? <AgentExecutionMetrics result={result} /> : null}

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

/**
 * 展示 Agent 正式样本的聚合过程事实与逐题诊断。
 *
 * @param props 包含 execution_summary 和可选样本诊断的完整结果。
 * @returns 向后兼容的紧凑指标区；旧结果缺少汇总字段时由调用方跳过。
 */
function AgentExecutionMetrics({ result }: { result: EvaluationResult }): JSX.Element {
  const summary = result.execution_summary;
  if (!summary) {
    return <></>;
  }
  const samples = (result.sample_results || []).filter((sample) => sample.diagnostics);
  const protocol = result.protocol_preflight;

  return (
    <section aria-labelledby="agent-execution-title" className="border-b border-border px-5 py-5 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 id="agent-execution-title" className="text-sm font-semibold text-ink">
          执行过程指标
        </h4>
        {protocol ? (
          <Badge
            tone={
              protocol.status === "compatible"
                ? "success"
                : protocol.status === "degraded"
                  ? "warning"
                  : "danger"
            }
          >
            {protocolLabels[protocol.status]}
          </Badge>
        ) : null}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        <ProcessMetric label="工具调用" value={String(summary.total_tool_calls)} />
        <ProcessMetric label="平均耗时" value={`${summary.average_wall_time_seconds.toFixed(2)} 秒`} />
        <ProcessMetric label="改动文件" value={String(summary.total_changed_files)} />
        <ProcessMetric label="工具错误" value={String(summary.total_tool_errors)} />
      </div>

      {samples.length > 0 ? (
        <div className="mt-4 overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[620px] text-left text-xs">
            <thead className="bg-slate-50 text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">样本</th>
                <th className="px-3 py-2 font-medium">结果</th>
                <th className="px-3 py-2 text-right font-medium">工具</th>
                <th className="px-3 py-2 text-right font-medium">耗时</th>
                <th className="px-3 py-2 font-medium">改动文件</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {samples.map((sample) => {
                const diagnostics = sample.diagnostics!;
                return (
                  <tr key={sample.sample_id}>
                    <td className="px-3 py-2 font-mono text-ink">{sample.sample_id}</td>
                    <td className="px-3 py-2 text-ink">{outcomeLabels[diagnostics.outcome]}</td>
                    <td className="px-3 py-2 text-right font-mono">{diagnostics.tool_call_count}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      {diagnostics.wall_time_seconds.toFixed(2)} 秒
                    </td>
                    <td className="max-w-64 truncate px-3 py-2 font-mono text-muted">
                      {diagnostics.changed_files.join(", ") || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function ProcessMetric({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="rounded-md border border-border bg-slate-50/60 px-3 py-3">
      <span className="text-xs text-muted">{label}</span>
      <strong className="mt-1 block font-mono text-sm font-semibold text-ink">{value}</strong>
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
  const answerProtocols = Object.entries(reproducibility.answer_protocol_versions || {});
  const generationConfigs = Object.entries(reproducibility.generation_configs || {});

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
        <LedgerRow label="模型生成协议" value={reproducibility.model_generation_protocol_version} mono />
        <LedgerRow label="回答协议" value={answerProtocols.map(([key, value]) => `${key}: ${value}`).join(" · ")} />
        <LedgerRow
          label="分项生成配置"
          value={generationConfigs
            .map(([benchmark, config]) => (
              `${benchmark}: ${Object.entries(config).map(([key, value]) => `${key}=${value}`).join(", ")}`
            ))
            .join(" · ")}
        />
      </dl>
    </details>
  );
}

function isHexagonResult(result: EvaluationResult, isHexagon: boolean): boolean {
  return (
    isHexagon ||
    result.suite_id === "evalhub-hexagon-v1" ||
    (typeof result.dataset === "string" && result.dataset.startsWith("hexagon-")) ||
    (typeof result.benchmark === "string" && result.benchmark.includes("六边形"))
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
  value: unknown;
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
  const displayValue = typeof value === "string" ? value : "—";

  return (
    <div className="min-w-0 border-b border-border px-5 py-4 last:border-b-0 sm:nth-[2n]:border-l lg:border-b-0 lg:border-l lg:first:border-l-0 sm:px-6">
      <span className="block text-xs font-medium text-muted">{label}</span>
      <strong
        className={`${mono ? "font-mono text-xs" : "text-base"} ${accent ? "text-primary" : "text-ink"} mt-2 block truncate font-semibold tracking-tight`}
        title={displayValue}
      >
        {displayValue}
      </strong>
      {meta ? <span className="mt-1 block text-xs font-medium text-blue-600">{meta}</span> : null}
    </div>
  );
}
