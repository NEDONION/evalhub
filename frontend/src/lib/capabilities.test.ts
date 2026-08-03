import { expect, it } from "vitest";

import type { ModelCapabilityProfile } from "../types";
import { CAPABILITY_ORDER, capabilityRadarPoints } from "./capabilities";

const profile: ModelCapabilityProfile = {
  suite_id: "llm-industry-core-v1",
  suite_version: "1.0.0",
  model: "local-test",
  generated_at: "2026-08-04T02:00:00+00:00",
  status: "partial",
  counts: { success: 2, failed: 0, blocked: 11 },
  capabilities: Object.fromEntries(
    CAPABILITY_ORDER.map((key, index) => [
      key,
      {
        label: key,
        score: index === 0 ? 100 : index === 1 ? null : 50,
        status: index === 1 ? "unassessed" : "partial",
        coverage: 0.5,
        benchmark_results: [],
      },
    ]),
  ),
};

it("builds a stable six-axis polygon and keeps unassessed dimensions at the center", () => {
  expect(CAPABILITY_ORDER).toHaveLength(6);
  expect(capabilityRadarPoints(profile, 100, 80)).toEqual([
    [100, 20],
    [100, 100],
    [134.64, 120],
    [100, 140],
    [65.36, 120],
    [65.36, 80],
  ]);
});
