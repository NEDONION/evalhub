export interface DownloadRange {
  minimumSeconds: number;
  maximumSeconds: number;
}

const DECIMAL_KB = 1_000;
const DECIMAL_MB = 1_000_000;
const DECIMAL_GB = 1_000_000_000;
const MINIMUM_BITS_PER_SECOND = 20_000_000;
const MAXIMUM_BITS_PER_SECOND = 100_000_000;

/**
 * 使用十进制容量单位格式化字节数，保证模型元数据与 Ollama 下载遥测口径一致。
 *
 * @param bytes 要展示的字节数；未知、负数或非有限值会返回破折号。
 * @returns 适合控制台紧凑展示的 B、KB、MB 或 GB 文本。
 */
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

/**
 * 把每秒字节数格式化为与容量一致的传输速率。
 *
 * @param bytesPerSecond Ollama 下载任务报告的瞬时字节速率。
 * @returns 带 `/s` 后缀的速率；未知值保持为破折号。
 */
export function formatRate(bytesPerSecond: number | null | undefined): string {
  const formatted = formatBytes(bytesPerSecond);
  return formatted === "—" ? formatted : `${formatted}/s`;
}

/**
 * 把秒数向上取整为中文短时长，避免向用户承诺低于实际值的剩余时间。
 *
 * @param seconds 预估下载秒数或服务端 ETA。
 * @returns 秒或分秒组合；未知或非法值返回破折号。
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.ceil(seconds);
  if (rounded < 60) return `${rounded} 秒`;
  const minutes = Math.floor(rounded / 60);
  const remainingSeconds = rounded % 60;
  return remainingSeconds === 0 ? `${minutes} 分` : `${minutes} 分 ${remainingSeconds} 秒`;
}

/**
 * 按 20–100 Mbps 的透明假设估算模型下载时间范围。
 *
 * @param bytes 模型实际或推荐配置中的预估容量。
 * @returns 最快和最慢秒数；容量未知或非法时返回 `null`。
 */
export function estimateDownloadRange(bytes: number | null | undefined): DownloadRange | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return null;
  return {
    minimumSeconds: Math.ceil((bytes * 8) / MAXIMUM_BITS_PER_SECOND),
    maximumSeconds: Math.ceil((bytes * 8) / MINIMUM_BITS_PER_SECOND),
  };
}
