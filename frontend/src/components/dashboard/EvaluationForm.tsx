import { DatabaseZap, Play, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { buildEvaluationRequest, type EvaluationFormValues, validateEvaluation } from "../../lib/evaluation";
import type {
  AdapterType,
  AgentDifficulty,
  BenchmarkDefinition,
  BenchmarkSuite,
  Dataset,
  DatasetName,
  EvaluationRequest,
  EvaluationType,
  ModelOption,
  SampleMode,
} from "../../types";
import { Button } from "../ui/Button";
import { FieldMessage } from "../ui/FieldMessage";
import { Panel } from "../ui/Panel";
import { ModelSelector } from "./ModelSelector";

interface EvaluationFormProps {
  datasets: Dataset[];
  benchmarks: BenchmarkDefinition[];
  suites: BenchmarkSuite[];
  modelOptions: ModelOption[];
  model: string;
  baseUrl: string;
  running: boolean;
  preparing: boolean;
  onModelChange: (model: string) => void;
  onBaseUrlCommit: (baseUrl: string) => void;
  onManageAssets: () => void;
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

const agentDifficulties: Array<{ value: AgentDifficulty; label: string; meta: string }> = [
  { value: "all", label: "全部难度", meta: "6 个任务" },
  { value: "easy", label: "简单", meta: "2 个任务" },
  { value: "medium", label: "中等", meta: "2 个任务" },
  { value: "hard", label: "困难", meta: "2 个任务" },
];

/**
 * 渲染模型与 Agent 两类评测配置，并在提交前校验样本数量和 Ollama 模型可用性。
 *
 * @param props 数据集、模型、运行状态，以及资产管理、缓存和任务创建回调。
 * @returns 仅提交后端真实支持组合的评测表单。
 */
export function EvaluationForm({
  datasets,
  benchmarks,
  suites,
  modelOptions,
  model,
  baseUrl,
  running,
  preparing,
  onModelChange,
  onBaseUrlCommit,
  onManageAssets,
  onPrepare,
  onSubmit,
}: EvaluationFormProps) {
  const [evaluationType, setEvaluationType] = useState<EvaluationType>("model");
  const [targetMode, setTargetMode] = useState<"single" | "suite">("single");
  const [suiteId, setSuiteId] = useState("llm-industry-core-v1");
  const [dataset, setDataset] = useState<DatasetName>("gsm8k");
  const [subject, setSubject] = useState("all");
  const [adapter, setAdapter] = useState<AdapterType>("ollama");
  const [sampleMode, setSampleMode] = useState<SampleMode>("all");
  const [agentDifficulty, setAgentDifficulty] = useState<AgentDifficulty>("all");
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

  const benchmarkOptions = useMemo<BenchmarkDefinition[]>(() => {
    if (benchmarks.length > 0) return benchmarks;
    return datasetOptions.map((item) => ({
      id: item.name,
      version: "local",
      display_name: item.display_name,
      capability: "",
      capability_label: "",
      dataset_source: item.source_url || "local",
      dataset_revision: "local",
      homepage: item.homepage || "",
      executor: "native",
      metric: "",
      locally_runnable: true,
      readiness_reason: null,
    }));
  }, [benchmarks, datasetOptions]);
  const selectedBenchmark = benchmarkOptions.find((item) => item.id === dataset) || null;
  const selectedSuite = suites.find((item) => item.id === suiteId) || suites[0] || null;

  const availableModels: ModelOption[] =
    modelOptions.length > 0
      ? modelOptions
      : [
          {
            name: model,
            label: model,
            description: "当前模型",
            installed: false,
            size_bytes: null,
            size_kind: "unknown" as const,
            evaluation_types: ["model", "agent"],
            capability_label: "当前模型",
          },
        ];
  const applicableModels = availableModels.filter((option) => option.evaluation_types.includes(evaluationType));
  const selectedModelOption = applicableModels.find((option) => option.name === model);
  const missingOllamaModel = (evaluationType === "agent" || adapter === "ollama") && !selectedModelOption?.installed;

  const values: EvaluationFormValues = {
    evaluationType,
    dataset,
    subject,
    adapter,
    model,
    baseUrl: baseUrlDraft,
    sampleMode,
    agentDifficulty,
    limit,
    suiteId: targetMode === "suite" ? selectedSuite?.id || null : null,
  };

  /**
   * 校验当前表单并提交稳定的模型或 Agent 请求。
   *
   * @param event 浏览器表单提交事件，用于阻止页面刷新。
   */
  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validateEvaluation(values);
    if (missingOllamaModel) nextErrors.model = "先下载模型或选择已安装模型";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    onBaseUrlCommit(baseUrlDraft);
    onSubmit(buildEvaluationRequest(values));
  }

  /**
   * 切换评测对象，并把 Agent 模式收敛到当前后端真实支持的全量筛选与 Ollama 组合。
   *
   * @param event 评测类型 radio 的变更事件。
   */
  function changeEvaluationType(event: React.ChangeEvent<HTMLInputElement>) {
    const nextType = event.target.value as EvaluationType;
    const nextModels = availableModels.filter((option) => option.evaluation_types.includes(nextType));
    const currentModel = nextModels.find((option) => option.name === model);
    const fallbackModel = nextModels.find((option) => option.installed) || nextModels[0];
    setEvaluationType(nextType);
    setErrors({});
    if (!currentModel && fallbackModel) onModelChange(fallbackModel.name);
    if (nextType === "agent") {
      setAdapter("ollama");
      setSampleMode("all");
    }
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
        <fieldset className="border-b border-border px-5 py-4 sm:px-6 lg:col-span-2">
          <legend className="sr-only">评测对象</legend>
          <div className="inline-flex rounded-md border border-border bg-slate-50 p-1">
            {(["model", "agent"] as const).map((value) => (
              <label key={value} className="cursor-pointer">
                <input
                  type="radio"
                  name="evaluation-type"
                  value={value}
                  aria-label={value === "model" ? "模型评测" : "Agent 评测"}
                  checked={evaluationType === value}
                  onChange={changeEvaluationType}
                  className="peer sr-only"
                />
                <span className="block rounded px-4 py-2 text-xs font-semibold text-muted transition-colors peer-checked:bg-white peer-checked:text-primary peer-checked:shadow-sm">
                  {value === "model" ? "模型评测" : "Agent 评测"}
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="space-y-4 border-b border-border p-5 sm:p-6 lg:border-r lg:border-b-0">
          {evaluationType === "model" ? (
            <>
              <fieldset>
                <legend className="mb-1.5 text-xs font-medium text-muted">评测范围</legend>
                <div className="grid grid-cols-2 rounded-md border border-border bg-slate-50 p-1">
                  {(["single", "suite"] as const).map((value) => (
                    <label key={value} className="cursor-pointer">
                      <input
                        type="radio"
                        name="target-mode"
                        value={value}
                        checked={targetMode === value}
                        onChange={() => setTargetMode(value)}
                        className="peer sr-only"
                      />
                      <span className="block rounded px-3 py-2 text-center text-xs font-semibold text-muted peer-checked:bg-white peer-checked:text-primary peer-checked:shadow-sm">
                        {value === "single" ? "单项 Benchmark" : "行业能力套件"}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              {targetMode === "suite" ? (
                <div>
                  <label htmlFor="suite" className="mb-1.5 block text-xs font-medium text-muted">
                    评测套件
                  </label>
                  <select
                    id="suite"
                    className={controlClass}
                    value={selectedSuite?.id || suiteId}
                    onChange={(event) => setSuiteId(event.target.value)}
                    disabled={suites.length === 0}
                  >
                    {suites.length === 0 ? <option>正在读取 Registry</option> : null}
                    {suites.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.display_name}
                      </option>
                    ))}
                  </select>
                  {selectedSuite ? (
                    <p
                      className={`mt-2 text-xs leading-5 ${
                        (selectedSuite.ready_count ?? selectedSuite.locally_runnable_count) ===
                        selectedSuite.benchmark_count
                          ? "text-emerald-700"
                          : "text-amber-700"
                      }`}
                    >
                      当前已就绪 {selectedSuite.ready_count ?? selectedSuite.locally_runnable_count} / {selectedSuite.benchmark_count} 个执行器
                      {(selectedSuite.ready_count ?? selectedSuite.locally_runnable_count) ===
                      selectedSuite.benchmark_count
                        ? "，全部可运行。"
                        : "；其余节点会明确记录为阻塞，不会产生虚假分数。"}
                    </p>
                  ) : null}
                </div>
              ) : (
              <div>
                <label htmlFor="dataset" className="mb-1.5 block text-xs font-medium text-muted">
                  Benchmark
                </label>
                <select
                  id="dataset"
                  aria-label="数据集"
                  className={controlClass}
                  value={dataset}
                  onChange={(event) => {
                    const nextDataset = event.target.value as DatasetName;
                    const nextBenchmark = benchmarkOptions.find((item) => item.id === nextDataset);
                    setDataset(nextDataset);
                    if (nextBenchmark?.executor !== "native") setAdapter("ollama");
                  }}
                >
                  {benchmarkOptions.map((item) => (
                    <option key={item.id} value={item.id} disabled={!item.locally_runnable}>
                      {item.display_name} · {item.capability_label || "本地"}
                      {item.locally_runnable ? "" : "（未就绪）"}
                    </option>
                  ))}
                </select>
              </div>
              )}

              {targetMode === "single" && dataset === "mmlu" ? (
                <div>
                  <label htmlFor="subject" className="mb-1.5 block text-xs font-medium text-muted">
                    MMLU 学科
                  </label>
                  <input
                    id="subject"
                    className={controlClass}
                    value={subject}
                    onChange={(event) => setSubject(event.target.value)}
                  />
                </div>
              ) : null}

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
                  <option value="oracle" disabled={selectedBenchmark?.executor !== "native"}>
                    Oracle 管线自检
                  </option>
                </select>
              </div>
            </>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-blue-100 bg-blue-50/55 p-4">
                <span className="block text-[10px] font-semibold tracking-[0.1em] text-blue-600 uppercase">
                  Benchmark
                </span>
                <strong className="mt-1 block text-sm text-blue-950">EvalHub Coding Mini</strong>
                <span className="mt-1 block text-xs leading-5 text-blue-700">6 个三级难度隐藏校验任务</span>
              </div>
              <div className="rounded-md border border-blue-100 bg-blue-50/55 p-4">
                <span className="block text-[10px] font-semibold tracking-[0.1em] text-blue-600 uppercase">
                  Agent shell
                </span>
                <strong className="mt-1 block text-sm text-blue-950">Pi CLI</strong>
                <span className="mt-1 block text-xs leading-5 text-blue-700">
                  固定工具与 macOS workspace-write 沙箱
                </span>
              </div>
            </div>
          )}

          <div>
            <ModelSelector
              id="model"
              label={evaluationType === "agent" ? "Agent 基模" : "模型"}
              options={applicableModels}
              value={model}
              describedBy={missingOllamaModel ? "model-error" : undefined}
              onChange={onModelChange}
            />
            {missingOllamaModel ? (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <FieldMessage id="model-error">先下载模型或选择已安装模型</FieldMessage>
                <Button size="sm" variant="ghost" className="h-7 px-2 text-primary" onClick={onManageAssets}>
                  前往资产管理
                </Button>
              </div>
            ) : null}
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
          {evaluationType === "model" ? (
            <>
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
                          // 切换样本范围时清除只属于上一次自定义输入的校验消息。
                          setSampleMode(option.value);
                          setErrors({});
                        }}
                        className="peer sr-only"
                      />
                      <span className="flex min-h-16 flex-col justify-center rounded-md border border-border bg-white px-3 transition-colors peer-checked:border-blue-300 peer-checked:bg-blue-50 peer-checked:shadow-[inset_0_0_0_1px_rgba(37,99,235,0.08)]">
                        <strong className="text-xs font-semibold text-ink peer-checked:text-primary">
                          {option.label}
                        </strong>
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
            </>
          ) : (
            <div className="space-y-5">
              <fieldset>
                <legend className="mb-3 text-xs font-medium text-muted">任务难度</legend>
                <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
                  {agentDifficulties.map((option) => (
                    <label key={option.value} className="relative cursor-pointer">
                      <input
                        type="radio"
                        name="agent-difficulty"
                        value={option.value}
                        aria-label={option.label}
                        checked={agentDifficulty === option.value}
                        onChange={() => setAgentDifficulty(option.value)}
                        className="peer sr-only"
                      />
                      <span className="flex min-h-16 flex-col justify-center rounded-md border border-border bg-white px-3 transition-colors peer-checked:border-blue-300 peer-checked:bg-blue-50">
                        <strong className="text-xs font-semibold text-ink peer-checked:text-primary">
                          {option.label}
                        </strong>
                        <span className="mt-1 text-[11px] text-slate-400">{option.meta}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <div>
                <p className="text-xs font-medium text-muted">本次 Agent 评测流程</p>
                <ol className="mt-3 space-y-2 text-xs leading-5 text-slate-600">
                  <li className="rounded-md border border-border bg-slate-50 px-3 py-2">1. 创建独立 Git 样本工作区</li>
                  <li className="rounded-md border border-border bg-slate-50 px-3 py-2">2. Pi 使用所选基模完成任务</li>
                  <li className="rounded-md border border-border bg-slate-50 px-3 py-2">3. 隐藏 Verifier 评分并聚合六维能力</li>
                </ol>
              </div>
            </div>
          )}

          <div className="mt-auto pt-7">
            <div className="mb-4 rounded-md border border-blue-100 bg-blue-50/65 p-3 text-xs leading-5 text-blue-800">
              {evaluationType === "agent"
                ? "按所选难度运行 Coding Mini；最终消息不会直接参与得分。"
                : sampleMode === "all"
                ? "将运行完整数据集，耗时取决于模型和本地设备。"
                : sampleMode === "quick"
                  ? "固定运行 5 条样本，适合验证链路是否正常。"
                  : `将运行前 ${limit || "—"} 条样本。`}
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              {evaluationType === "model" ? (
                <Button
                  variant="secondary"
                  onClick={() => (targetMode === "suite" ? onManageAssets() : onPrepare(dataset))}
                  disabled={preparing || running}
                >
                  <DatabaseZap className="h-4 w-4" aria-hidden="true" />
                  {targetMode === "suite"
                    ? "查看本地资产"
                    : preparing
                      ? "正在缓存"
                      : "缓存当前数据集"}
                </Button>
              ) : null}
              <Button type="submit" disabled={running || preparing || missingOllamaModel}>
                <Play className="h-4 w-4" aria-hidden="true" />
                {running ? "正在评测" : evaluationType === "agent" ? "发起 Agent 评测" : "发起评测"}
              </Button>
            </div>
          </div>
        </div>
      </form>
    </Panel>
  );
}
