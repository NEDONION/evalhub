import { useMemo, useState } from "react";

import { Header } from "./components/dashboard/Header";
import { DatasetTable } from "./components/dashboard/DatasetTable";
import { EvaluationForm } from "./components/dashboard/EvaluationForm";
import { MetricStrip } from "./components/dashboard/MetricStrip";
import { OllamaPanel } from "./components/dashboard/OllamaPanel";
import { useEvalHub } from "./hooks/useEvalHub";
import { formatScore } from "./lib/evaluation";

const DEFAULT_MODEL = "qwen2.5:0.5b";
const DEFAULT_BASE_URL = "http://127.0.0.1:11434";

export default function App() {
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const dashboard = useEvalHub(model, baseUrl);

  const ollamaState = useMemo(() => {
    if (dashboard.ollamaError) return "offline" as const;
    if (!dashboard.ollama) return "loading" as const;
    if (dashboard.ollama.installed && dashboard.ollama.running && dashboard.ollama.model_present) {
      return "ready" as const;
    }
    return dashboard.ollama.running ? ("warning" as const) : ("offline" as const);
  }, [dashboard.ollama, dashboard.ollamaError]);

  return (
    <div className="min-h-screen bg-page text-ink">
      <Header health={dashboard.health} refreshing={dashboard.refreshing} onRefresh={() => void dashboard.refresh()} />

      <main className="mx-auto max-w-[1440px] px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        <section className="mb-7 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <p className="mb-2 text-[11px] font-semibold tracking-[0.14em] text-primary uppercase">Evaluation workspace</p>
            <h1 className="text-3xl font-semibold tracking-[-0.04em] text-ink sm:text-4xl">模型评测工作台</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted sm:text-base">
              从本地运行时就绪到 Benchmark 得分，在一个真实、可追踪的流程里完成模型验证。
            </p>
          </div>
          <p className="max-w-sm border-l-2 border-primary pl-4 text-sm leading-6 text-muted">
            先检查运行环境，再选择数据集与样本范围。页面只呈现当前可执行的能力。
          </p>
        </section>

        <div className="space-y-5">
          <MetricStrip
            health={dashboard.health}
            ollamaState={ollamaState}
            datasetCount={dashboard.datasets.length}
            preparedCount={dashboard.datasets.filter((dataset) => dataset.prepared).length}
            latestScore={dashboard.result ? formatScore(dashboard.result.average_score) : "—"}
          />
          <OllamaPanel status={dashboard.ollama} loading={dashboard.refreshing} error={dashboard.ollamaError} />
          <EvaluationForm
            datasets={dashboard.datasets}
            modelOptions={dashboard.ollama?.model_options || []}
            model={model}
            baseUrl={baseUrl}
            running={dashboard.runningEvaluation}
            preparing={Boolean(dashboard.preparingDataset)}
            onModelChange={setModel}
            onBaseUrlCommit={setBaseUrl}
            onPrepare={(dataset) => void dashboard.prepare(dataset)}
            onSubmit={(request) => void dashboard.run(request)}
          />
          <DatasetTable
            datasets={dashboard.datasets}
            preparingDataset={dashboard.preparingDataset}
            error={dashboard.datasetError}
            onPrepare={(dataset) => void dashboard.prepare(dataset)}
          />
        </div>
      </main>
    </div>
  );
}
