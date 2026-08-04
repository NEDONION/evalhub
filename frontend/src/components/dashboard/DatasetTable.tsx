import { CheckCircle2, Download, ExternalLink, FolderCheck, RefreshCw } from "lucide-react";

import type { Dataset, DatasetName } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";

interface DatasetTableProps {
  datasets: Dataset[];
  preparingDataset: DatasetName | null;
  error: string | null;
  notice: string | null;
  onPrepare: (dataset: DatasetName, force: boolean) => void;
}

const taskLabels: Record<string, string> = {
  math_reasoning: "数学推理",
  multiple_choice: "多选问答",
};

const executorLabels = {
  native: "EvalHub 原生",
  lm_eval: "lm-eval",
  sandboxed_code: "Docker 沙箱",
};

const metricLabels: Record<string, string> = {
  numeric_exact_match: "数值匹配",
  choice_letter: "选项匹配",
  exact_match: "精确匹配",
};

/**
 * 展示公开 Benchmark 的来源、本地缓存状态与样本规模，并区分首次缓存和强制更新。
 *
 * @param datasets 后端返回的数据集目录状态。
 * @param preparingDataset 当前正在执行缓存操作的数据集名称，用于禁用并标记全部操作按钮。
 * @param error 最近一次目录读取或准备失败信息。
 * @param notice 最近一次成功缓存或更新的确认信息。
 * @param onPrepare 用户触发资产操作时的回调；第二个参数在已缓存数据集上为 `true`，
 * 表示必须重新下载、校验并原子替换现有缓存。
 */
export function DatasetTable({ datasets, preparingDataset, error, notice, onPrepare }: DatasetTableProps) {
  return (
    <Panel aria-labelledby="datasets-title" className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-border px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div>
          <p className="mb-1 flex items-center gap-2 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">
            <FolderCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Benchmark assets
          </p>
          <h2 id="datasets-title" className="text-base font-semibold tracking-tight text-ink">
            数据集资产
          </h2>
          <p className="mt-1 text-sm text-muted">公开 Benchmark 的本地缓存与来源。</p>
        </div>
        <span className="font-mono text-xs text-slate-400">{datasets.length} DATASETS</span>
      </div>

      {error ? (
        <div role="alert" className="border-b border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700 sm:px-6">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div role="status" className="flex items-center gap-2 border-b border-emerald-100 bg-emerald-50 px-5 py-3 text-sm text-emerald-700 sm:px-6">
          <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
          {notice}
        </div>
      ) : null}

      <div role="table" aria-label="数据集资产">
        <div role="row" className="dataset-grid hidden border-b border-border bg-slate-50/70 px-5 py-2.5 text-[11px] font-semibold tracking-[0.06em] text-slate-400 uppercase md:grid sm:px-6">
          <span role="columnheader">数据集</span>
          <span role="columnheader">任务 / 指标</span>
          <span role="columnheader">样本</span>
          <span role="columnheader">状态</span>
          <span role="columnheader" className="text-right">操作</span>
        </div>

        {datasets.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted sm:px-6">正在读取数据集状态。</div>
        ) : (
          datasets.map((dataset) => {
            const preparing = preparingDataset === dataset.name;
            const runnable = dataset.locally_runnable !== false;
            const executor = executorLabels[dataset.executor || "native"];
            const status = !runnable ? "未就绪" : dataset.prepared ? "已缓存" : "未缓存";
            return (
              <div key={dataset.name} role="row" className="dataset-grid space-y-3 border-b border-border px-5 py-4 last:border-b-0 md:grid md:items-center md:gap-4 md:space-y-0 sm:px-6">
                <div role="cell" className="min-w-0">
                  <div className="flex items-center gap-2">
                    <strong className="truncate text-sm font-semibold text-ink">{dataset.display_name}</strong>
                    <a
                      href={dataset.homepage}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`查看 ${dataset.display_name}数据来源`}
                      className="shrink-0 text-slate-400 transition-colors hover:text-primary"
                    >
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    </a>
                  </div>
                  <p className="mt-1 truncate text-xs text-muted" title={dataset.local_path}>
                    {dataset.local_path}
                  </p>
                </div>
                <div role="cell" className="min-w-0 text-xs text-muted">
                  <span className="block text-ink">
                    {dataset.capability_label || taskLabels[dataset.task_type] || dataset.task_type}
                  </span>
                  <span className="mt-1 block text-slate-400">
                    {executor} · {metricLabels[dataset.evaluator_type] || dataset.evaluator_type}
                  </span>
                </div>
                <div role="cell" className="text-sm font-medium text-ink">
                  {dataset.sample_count === null ? "—" : dataset.sample_count.toLocaleString("zh-CN")}
                </div>
                <div role="cell">
                  <Badge tone={!runnable ? "warning" : dataset.prepared ? "success" : "neutral"} dot>
                    {status}
                  </Badge>
                </div>
                <div role="cell" className="flex justify-start md:justify-end">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => onPrepare(dataset.name, dataset.prepared)}
                    disabled={Boolean(preparingDataset) || !runnable}
                    title={dataset.readiness_reason || undefined}
                    aria-label={`${dataset.prepared ? "更新" : "缓存"} ${dataset.display_name}`}
                  >
                    {dataset.prepared ? (
                      <RefreshCw className={preparing ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} aria-hidden="true" />
                    ) : (
                      <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    {preparing ? (dataset.prepared ? "更新中" : "缓存中") : dataset.prepared ? "更新" : "缓存"}
                  </Button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </Panel>
  );
}
