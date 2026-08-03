import { DatabaseZap, Play, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { formatBytes } from "../../lib/assets";
import { buildEvaluationRequest, type EvaluationFormValues, validateEvaluation } from "../../lib/evaluation";
import type { AdapterType, Dataset, DatasetName, EvaluationRequest, ModelOption, SampleMode } from "../../types";
import { Button } from "../ui/Button";
import { FieldMessage } from "../ui/FieldMessage";
import { Panel } from "../ui/Panel";

interface EvaluationFormProps {
  datasets: Dataset[];
  modelOptions: ModelOption[];
  model: string;
  baseUrl: string;
  running: boolean;
  preparing: boolean;
  onModelChange: (model: string) => void;
  onBaseUrlCommit: (baseUrl: string) => void;
  onPrepare: (dataset: DatasetName) => void;
  onSubmit: (request: EvaluationRequest) => void;
}

const controlClass =
  "h-10 w-full rounded-md border border-border bg-white px-3 text-sm text-ink shadow-[0_1px_2px_rgba(15,23,42,0.025)] transition-colors placeholder:text-slate-400 hover:border-slate-300 focus:border-primary";

const sampleModes: Array<{ value: SampleMode; label: string; meta: string }> = [
  { value: "all", label: "全部样本", meta: "完整 Benchmark" },
  { value: "quick", label: "快速试跑", meta: "固定 5 条" },
  { value: "custom", label: "自定义", meta: "指定数量" },
];

export function EvaluationForm({
  datasets,
  modelOptions,
  model,
  baseUrl,
  running,
  preparing,
  onModelChange,
  onBaseUrlCommit,
  onPrepare,
  onSubmit,
}: EvaluationFormProps) {
  const [dataset, setDataset] = useState<DatasetName>("gsm8k");
  const [subject, setSubject] = useState("abstract_algebra");
  const [adapter, setAdapter] = useState<AdapterType>("ollama");
  const [sampleMode, setSampleMode] = useState<SampleMode>("all");
  const [limit, setLimit] = useState("20");
  const [baseUrlDraft, setBaseUrlDraft] = useState(baseUrl);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setBaseUrlDraft(baseUrl);
  }, [baseUrl]);

  const datasetOptions = useMemo(() => {
    if (datasets.length > 0) return datasets;
    return [
      { name: "gsm8k", display_name: "GSM8K 测试集" },
      { name: "mmlu", display_name: "MMLU 测试集" },
    ] as Dataset[];
  }, [datasets]);

  const availableModels =
    modelOptions.length > 0
      ? modelOptions
      : [{ name: model, label: model, description: "当前模型", installed: false, size_bytes: null, size_kind: "unknown" as const }];
  const selectedModelOption = availableModels.find((option) => option.name === model);
  const missingOllamaModel = adapter === "ollama" && !selectedModelOption?.installed;

  const values: EvaluationFormValues = {
    dataset,
    subject,
    adapter,
    model,
    baseUrl: baseUrlDraft,
    sampleMode,
    limit,
  };

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validateEvaluation(values);
    if (missingOllamaModel) nextErrors.model = "先下载模型或选择已安装模型";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    onBaseUrlCommit(baseUrlDraft);
    onSubmit(buildEvaluationRequest(values));
  }

  return (
    <Panel aria-labelledby="evaluation-form-title" className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-border px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div>
          <p className="mb-1 flex items-center gap-2 text-[11px] font-semibold tracking-[0.12em] text-primary uppercase">
            <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
            Evaluation setup
          </p>
          <h2 id="evaluation-form-title" className="text-base font-semibold tracking-tight text-ink">
            新建评测
          </h2>
          <p className="mt-1 text-sm text-muted">选择真实数据集、运行时和样本范围。</p>
        </div>
        <span className="font-mono text-xs text-slate-400">RUN / LOCAL</span>
      </div>

      <form onSubmit={submit} noValidate className="grid lg:grid-cols-2">
        <div className="space-y-4 border-b border-border p-5 sm:p-6 lg:border-r lg:border-b-0">
          <div>
            <label htmlFor="dataset" className="mb-1.5 block text-xs font-medium text-muted">
              数据集
            </label>
            <select
              id="dataset"
              className={controlClass}
              value={dataset}
              onChange={(event) => setDataset(event.target.value as DatasetName)}
            >
              {datasetOptions.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.display_name}
                </option>
              ))}
            </select>
          </div>

          {dataset === "mmlu" ? (
            <div>
              <label htmlFor="subject" className="mb-1.5 block text-xs font-medium text-muted">
                MMLU 学科
              </label>
              <input id="subject" className={controlClass} value={subject} onChange={(event) => setSubject(event.target.value)} />
            </div>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="adapter" className="mb-1.5 block text-xs font-medium text-muted">
                模型适配器
              </label>
              <select
                id="adapter"
                className={controlClass}
                value={adapter}
                onChange={(event) => setAdapter(event.target.value as AdapterType)}
              >
                <option value="ollama">Ollama 本地模型</option>
                <option value="oracle">Oracle 管线自检</option>
              </select>
            </div>
            <div>
              <label htmlFor="model" className="mb-1.5 block text-xs font-medium text-muted">
                模型
              </label>
              <select id="model" className={controlClass} value={model} onChange={(event) => onModelChange(event.target.value)}>
                {availableModels.map((option) => (
                  <option key={option.name} value={option.name}>
                    {option.label} · {formatBytes(option.size_bytes)} · {option.installed ? "已安装" : "未下载"}
                  </option>
                ))}
              </select>
              {missingOllamaModel ? <FieldMessage id="model-error">先下载模型或选择已安装模型</FieldMessage> : null}
            </div>
          </div>

          <div>
            <label htmlFor="base-url" className="mb-1.5 block text-xs font-medium text-muted">
              Ollama 地址
            </label>
            <input
              id="base-url"
              className={`${controlClass} font-mono text-xs`}
              value={baseUrlDraft}
              onChange={(event) => setBaseUrlDraft(event.target.value)}
              onBlur={() => onBaseUrlCommit(baseUrlDraft)}
            />
          </div>
        </div>

        <div className="flex min-w-0 flex-col p-5 sm:p-6">
          <fieldset>
            <legend className="mb-3 text-xs font-medium text-muted">样本范围</legend>
            <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
              {sampleModes.map((option) => (
                <label key={option.value} className="relative cursor-pointer">
                  <input
                    type="radio"
                    name="sample-mode"
                    value={option.value}
                    aria-label={option.label}
                    checked={sampleMode === option.value}
                    onChange={() => {
                      setSampleMode(option.value);
                      setErrors({});
                    }}
                    className="peer sr-only"
                  />
                  <span className="flex min-h-16 flex-col justify-center rounded-md border border-border bg-white px-3 transition-colors peer-checked:border-blue-300 peer-checked:bg-blue-50 peer-checked:shadow-[inset_0_0_0_1px_rgba(37,99,235,0.08)]">
                    <strong className="text-xs font-semibold text-ink peer-checked:text-primary">{option.label}</strong>
                    <span className="mt-1 text-[11px] text-slate-400">{option.meta}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          {sampleMode === "custom" ? (
            <div className="mt-4">
              <label htmlFor="limit" className="mb-1.5 block text-xs font-medium text-muted">
                自定义样本数量
              </label>
              <input
                id="limit"
                type="number"
                min="1"
                step="1"
                className={controlClass}
                value={limit}
                aria-invalid={Boolean(errors.limit)}
                aria-describedby={errors.limit ? "limit-error" : undefined}
                onChange={(event) => setLimit(event.target.value)}
              />
              {errors.limit ? <FieldMessage id="limit-error">{errors.limit}</FieldMessage> : null}
            </div>
          ) : null}

          <div className="mt-auto pt-7">
            <div className="mb-4 rounded-md border border-blue-100 bg-blue-50/65 p-3 text-xs leading-5 text-blue-800">
              {sampleMode === "all"
                ? "将运行完整数据集，耗时取决于模型和本地设备。"
                : sampleMode === "quick"
                  ? "固定运行 5 条样本，适合验证链路是否正常。"
                  : `将运行前 ${limit || "—"} 条样本。`}
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button variant="secondary" onClick={() => onPrepare(dataset)} disabled={preparing || running}>
                <DatabaseZap className="h-4 w-4" aria-hidden="true" />
                {preparing ? "正在缓存" : "缓存当前数据集"}
              </Button>
              <Button type="submit" disabled={running || preparing || missingOllamaModel}>
                <Play className="h-4 w-4" aria-hidden="true" />
                {running ? "正在评测" : "发起评测"}
              </Button>
            </div>
          </div>
        </div>
      </form>
    </Panel>
  );
}
