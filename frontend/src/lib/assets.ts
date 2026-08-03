export interface DownloadRange {
  minimumSeconds: number;
  maximumSeconds: number;
}

const DECIMAL_KB = 1_000;
const DECIMAL_MB = 1_000_000;
const DECIMAL_GB = 1_000_000_000;
const MINIMUM_BITS_PER_SECOND = 20_000_000;
const MAXIMUM_BITS_PER_SECOND = 100_000_000;

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes >= DECIMAL_GB) return `${(bytes / DECIMAL_GB).toFixed(1)} GB`;
  if (bytes >= DECIMAL_MB) {
    const megabytes = bytes / DECIMAL_MB;
    return `${Number.isInteger(megabytes) ? megabytes.toFixed(0) : megabytes.toFixed(1)} MB`;
  }
  if (bytes >= DECIMAL_KB) return `${(bytes / DECIMAL_KB).toFixed(1)} KB`;
  return `${Math.round(bytes)} B`;
}

export function formatRate(bytesPerSecond: number | null | undefined): string {
  const formatted = formatBytes(bytesPerSecond);
  return formatted === "—" ? formatted : `${formatted}/s`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.ceil(seconds);
  if (rounded < 60) return `${rounded} 秒`;
  const minutes = Math.floor(rounded / 60);
  const remainingSeconds = rounded % 60;
  return remainingSeconds === 0 ? `${minutes} 分` : `${minutes} 分 ${remainingSeconds} 秒`;
}

export function estimateDownloadRange(bytes: number | null | undefined): DownloadRange | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return null;
  return {
    minimumSeconds: Math.ceil((bytes * 8) / MAXIMUM_BITS_PER_SECOND),
    maximumSeconds: Math.ceil((bytes * 8) / MINIMUM_BITS_PER_SECOND),
  };
}
