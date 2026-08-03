import type { ModelCapabilityProfile } from "../types";

export const CAPABILITY_ORDER = [
  "knowledge",
  "instruction_following",
  "mathematics",
  "reasoning",
  "coding",
  "safety_trust",
] as const;

/** 把固定六维能力分数转换为 SVG 雷达图坐标，未评测维度停留在中心。 */
export function capabilityRadarPoints(
  profile: ModelCapabilityProfile,
  center: number,
  radius: number,
): Array<[number, number]> {
  return CAPABILITY_ORDER.map((key, index) => {
    const rawScore = profile.capabilities[key]?.score;
    const score = rawScore === null || rawScore === undefined ? 0 : Math.min(100, Math.max(0, rawScore));
    const angle = (-90 + index * 60) * (Math.PI / 180);
    const distance = radius * (score / 100);
    return [
      Number((center + Math.cos(angle) * distance).toFixed(2)),
      Number((center + Math.sin(angle) * distance).toFixed(2)),
    ];
  });
}
