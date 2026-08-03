import { describe, expect, it } from "vitest";

import { estimateDownloadRange, formatBytes, formatDuration, formatRate } from "./assets";

describe("local asset formatting", () => {
  it("formats Ollama decimal byte sizes and transfer rates", () => {
    expect(formatBytes(986_000_000)).toBe("986 MB");
    expect(formatBytes(2_000_000_000)).toBe("2.0 GB");
    expect(formatBytes(null)).toBe("—");
    expect(formatRate(31_400_000)).toBe("31.4 MB/s");
    expect(formatRate(null)).toBe("—");
  });

  it("formats short and multi-minute durations without hiding remaining seconds", () => {
    expect(formatDuration(44)).toBe("44 秒");
    expect(formatDuration(79)).toBe("1 分 19 秒");
    expect(formatDuration(null)).toBe("—");
  });

  it("estimates a transparent 20 to 100 Mbps pre-download range", () => {
    expect(estimateDownloadRange(986_000_000)).toEqual({
      minimumSeconds: 79,
      maximumSeconds: 395,
    });
    expect(estimateDownloadRange(null)).toBeNull();
  });
});
