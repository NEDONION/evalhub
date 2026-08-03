import type { JSX } from "react";

import type { AgentCapabilityReport } from "../../types";

interface AgentCapabilityHexagonProps {
  report: AgentCapabilityReport;
}

interface ChartPoint {
  x: number;
  y: number;
}

const CHART_CENTER = 120;
const CHART_RADIUS = 82;
const AXIS_COUNT = 6;

/**
 * 把能力维度下标和归一化分数转换为雷达图坐标。
 *
 * @param index 固定六维顺序中的位置，从顶部开始顺时针排列。
 * @param ratio 0 到 1 的能力分数或网格比例。
 * @returns 可直接用于 SVG 点和轴线的二维坐标。
 */
function chartPoint(index: number, ratio: number): ChartPoint {
  const boundedRatio = Math.min(1, Math.max(0, ratio));
  const angle = ((-90 + index * 60) * Math.PI) / 180;
  return {
    x: CHART_CENTER + Math.cos(angle) * CHART_RADIUS * boundedRatio,
    y: CHART_CENTER + Math.sin(angle) * CHART_RADIUS * boundedRatio,
  };
}

/**
 * 生成一个六边形的 SVG points 字符串。
 *
 * @param ratios 六个轴各自的半径比例；缺失值安全按零分处理。
 * @returns 依次连接六个能力轴坐标的空格分隔字符串。
 */
function polygonPoints(ratios: number[]): string {
  return Array.from({ length: AXIS_COUNT }, (_unused, index) => {
    const point = chartPoint(index, ratios[index] ?? 0);
    return `${point.x},${point.y}`;
  }).join(" ");
}

/**
 * 将归一化分数转为用户容易比较的整数百分比。
 *
 * @param score 后端返回的 0 到 1 能力分数。
 * @returns 带百分号的整数文本。
 */
function formatCapabilityScore(score: number): string {
  return `${Math.round(Math.min(1, Math.max(0, score)) * 100)}%`;
}

/**
 * 展示 Agent 六维能力的原生 SVG 六边形和逐项文本分数。
 *
 * @param props 后端隐藏 Verifier 聚合出的固定六维能力报告。
 * @returns 无外部图表依赖、具备无障碍名称且移动端可读的报告区域。
 */
export function AgentCapabilityHexagon({ report }: AgentCapabilityHexagonProps): JSX.Element {
  const scores = report.dimensions.map((dimension) => dimension.score);
  const axisPoints = Array.from({ length: AXIS_COUNT }, (_unused, index) => chartPoint(index, 1));

  return (
    <section className="border-b border-border px-5 py-6 sm:px-6" aria-labelledby="agent-report-title">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[10px] font-semibold tracking-[0.12em] text-primary uppercase">
            Capability profile
          </p>
          <h4 id="agent-report-title" className="mt-1 text-sm font-semibold text-ink">
            Agent 能力报告
          </h4>
          <p className="mt-1 text-xs leading-5 text-muted">分数来自工作区最终状态的隐藏校验，不采用 Agent 自述。</p>
        </div>
        <div className="font-mono text-xs text-muted">
          综合分 <strong className="ml-1 text-base text-primary">{formatCapabilityScore(report.overall_score)}</strong>
        </div>
      </div>

      <div className="mt-5 grid items-center gap-6 md:grid-cols-[minmax(240px,0.85fr)_minmax(260px,1.15fr)]">
        <svg
          role="img"
          aria-label="Agent 六维能力图"
          viewBox="0 0 240 240"
          className="mx-auto h-auto w-full max-w-[280px]"
        >
          <title>Agent 六维能力图</title>
          {[0.25, 0.5, 0.75, 1].map((ratio) => (
            <polygon
              key={ratio}
              points={polygonPoints(Array(AXIS_COUNT).fill(ratio))}
              fill="none"
              stroke={ratio === 1 ? "#cbd5e1" : "#e2e8f0"}
              strokeWidth={ratio === 1 ? 1.25 : 1}
            />
          ))}
          {axisPoints.map((point, index) => (
            <line
              key={index}
              x1={CHART_CENTER}
              y1={CHART_CENTER}
              x2={point.x}
              y2={point.y}
              stroke="#e2e8f0"
              strokeWidth="1"
            />
          ))}
          <polygon
            points={polygonPoints(scores)}
            fill="#2563eb"
            fillOpacity="0.18"
            stroke="#2563eb"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          {scores.map((score, index) => {
            const point = chartPoint(index, score);
            return <circle key={index} cx={point.x} cy={point.y} r="3" fill="#1d4ed8" />;
          })}
        </svg>

        <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
          {report.dimensions.map((dimension) => (
            <div key={dimension.key} className="min-w-0">
              <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
                <span className="font-medium text-ink">{dimension.label}</span>
                <span className="font-mono font-semibold tabular-nums text-primary">
                  {formatCapabilityScore(dimension.score)}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-sm bg-slate-100" aria-hidden="true">
                <span
                  className="block h-full rounded-sm bg-primary"
                  style={{ width: formatCapabilityScore(dimension.score) }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
