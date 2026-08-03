import { ChartNoAxesCombined } from "lucide-react";
import type { JSX } from "react";

import { CAPABILITY_ORDER, capabilityRadarPoints } from "../../lib/capabilities";
import type { ModelCapabilityProfile } from "../../types";
import { Badge } from "../ui/Badge";

interface CapabilityRadarProps {
  profile: ModelCapabilityProfile;
}

interface RadarDimension {
  key: string;
  label: string;
  score: number | null;
  status: "complete" | "partial" | "unassessed";
  coverage: number;
  benchmark_results: Array<{ benchmark_id: string }>;
}

const CENTER = 180;
const RADIUS = 104;

/** 展示后端生成的固定六维模型能力画像及各维 Benchmark 覆盖率。 */
export function CapabilityRadar({ profile }: CapabilityRadarProps): JSX.Element {
  const points = capabilityRadarPoints(profile, CENTER, RADIUS);
  const pointString = points.map(([x, y]) => `${x},${y}`).join(" ");
  const dimensions: RadarDimension[] = CAPABILITY_ORDER.map((key) => ({
    key,
    label: profile.capabilities[key]?.label ?? key,
    score: profile.capabilities[key]?.score ?? null,
    status: profile.capabilities[key]?.status ?? "unassessed",
    coverage: profile.capabilities[key]?.coverage ?? 0,
    benchmark_results: profile.capabilities[key]?.benchmark_results ?? [],
  }));
  const assessedCount = dimensions.filter((item) => item.score !== null && item.score !== undefined).length;

  return (
    <section aria-labelledby="capability-radar-title" className="border-b border-border">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 sm:px-6">
        <div>
          <p className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.12em] text-primary uppercase">
            <ChartNoAxesCombined className="h-3.5 w-3.5" aria-hidden="true" />
            Capability profile
          </p>
          <h4 id="capability-radar-title" className="mt-1 text-sm font-semibold text-ink">
            LLM 六维能力画像
          </h4>
        </div>
        <Badge tone={profile.status === "complete" ? "success" : "warning"}>
          {profile.status === "complete" ? "完整画像" : `${assessedCount} / 6 已评测`}
        </Badge>
      </div>

      <div className="grid border-t border-border lg:grid-cols-[minmax(320px,440px)_minmax(0,1fr)]">
        <div className="flex min-h-[320px] items-center justify-center border-b border-border bg-slate-50/45 px-2 lg:border-r lg:border-b-0">
          <svg
            viewBox="0 0 360 320"
            className="h-auto w-full max-w-[390px]"
            role="img"
            aria-label="六维模型能力雷达图"
          >
            {[0.25, 0.5, 0.75, 1].map((scale) => (
              <polygon
                key={scale}
                points={ringPoints(scale)}
                fill="none"
                stroke={scale === 1 ? "#cbd5e1" : "#e2e8f0"}
                strokeWidth="1"
              />
            ))}
            {CAPABILITY_ORDER.map((key, index) => {
              const [x, y] = axisPoint(index);
              return <line key={key} x1={CENTER} y1={CENTER} x2={x} y2={y} stroke="#e2e8f0" />;
            })}
            <polygon
              points={pointString}
              fill="rgba(37, 99, 235, 0.16)"
              stroke="#2563eb"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {points.map(([x, y], index) => (
              <circle key={CAPABILITY_ORDER[index]} cx={x} cy={y} r="3" fill="#1d4ed8" />
            ))}
            {dimensions.map((item, index) => {
              const [x, y] = labelPoint(index);
              return (
                <text
                  key={item.key}
                  x={x}
                  y={y}
                  textAnchor={index === 1 || index === 2 ? "start" : index === 4 || index === 5 ? "end" : "middle"}
                  className="fill-slate-600 text-[11px] font-medium"
                >
                  {item.label || item.key}
                </text>
              );
            })}
          </svg>
        </div>

        <div className="divide-y divide-border">
          {dimensions.map((item) => (
            <div key={item.key} className="grid min-w-0 grid-cols-[minmax(0,1fr)_72px_78px] items-center gap-3 px-5 py-3 sm:px-6">
              <div className="min-w-0">
                <strong className="block truncate text-xs font-semibold text-ink">{item.label || item.key}</strong>
                <span className="mt-0.5 block truncate text-[11px] text-slate-400">
                  {item.benchmark_results.length} 个 Benchmark
                </span>
              </div>
              <span className="text-right font-mono text-sm font-semibold tabular-nums text-ink">
                {item.score === null || item.score === undefined ? "—" : item.score.toFixed(1)}
              </span>
              <span className="text-right text-[11px] tabular-nums text-muted">
                覆盖 {Math.round(item.coverage * 100)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function axisPoint(index: number, scale = 1): [number, number] {
  const angle = (-90 + index * 60) * (Math.PI / 180);
  return [CENTER + Math.cos(angle) * RADIUS * scale, CENTER + Math.sin(angle) * RADIUS * scale];
}

function ringPoints(scale: number): string {
  return CAPABILITY_ORDER.map((_, index) => axisPoint(index, scale).join(",")).join(" ");
}

function labelPoint(index: number): [number, number] {
  const [x, y] = axisPoint(index, 1.2);
  return [x, y + (index === 0 ? -2 : index === 3 ? 10 : 4)];
}
