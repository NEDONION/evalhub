import { useMemo, useState } from "react";

import { Header } from "./components/dashboard/Header";
import { DatasetTable } from "./components/dashboard/DatasetTable";
import { EvaluationForm } from "./components/dashboard/EvaluationForm";
import { MetricStrip } from "./components/dashboard/MetricStrip";
import { OllamaPanel } from "./components/dashboard/OllamaPanel";
import { OverviewPanel } from "./components/dashboard/OverviewPanel";
import { ResultPanel } from "./components/dashboard/ResultPanel";
import { SidebarNav, type WorkspaceView } from "./components/dashboard/SidebarNav";
import { useEvalHub } from "./hooks/useEvalHub";
import { formatScore } from "./lib/evaluation";

const DEFAULT_MODEL = "qwen2.5:0.5b";
const DEFAULT_BASE_URL = "http://127.0.0.1:11434";

const pageCopy: Record<WorkspaceView, { eyebrow: string; title: string; description: string }> = {
  overview: {
    eyebrow: "Workspace overview",
    title: "工作台概览",
    description: "检查当前就绪状态，再进入需要处理的工作区。",
  },
  evaluation: {
    eyebrow: "Evaluation setup",
    title: "发起评测",
    description: "选择真实 Benchmark、推理适配器和样本范围。",
  },
  assets: {
    eyebrow: "Local assets",
    title: "资产管理",
    description: "集中管理 Ollama 模型与公开 Benchmark 缓存。",
  },
  results: {
    eyebrow: "Evaluation output",
    title: "评测结果",
    description: "查看最近一次任务的聚合指标和失败样本。",
  },
};

export default function App() {
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const [currentView, setCurrentView] = useState<WorkspaceView>("overview");
  const [dismissedDownloadModel, setDismissedDownloadModel] = useState<string | null>(null);
  const dashboard = useEvalHub(model, baseUrl);
  const selectedModelOption = dashboard.ollama?.model_options.find((option) => option.name === model) || null;

  const ollamaState = useMemo(() => {
    if (dashboard.ollamaError) return "offline" as const;
    if (!dashboard.ollama) return "loading" as const;
    if (dashboard.ollama.installed && dashboard.ollama.running && dashboard.ollama.model_present) {
      return "ready" as const;
    }
    return dashboard.ollama.running ? ("warning" as const) : ("offline" as const);
  }, [dashboard.ollama, dashboard.ollamaError]);
  const pullActive = Boolean(
    dashboard.modelPullTask && ["pending", "pulling", "verifying"].includes(dashboard.modelPullTask.status),
  );
  const copy = pageCopy[currentView];

  return (
    <div className="min-h-screen bg-page text-ink">
      <Header health={dashboard.health} refreshing={dashboard.refreshing} onRefresh={() => void dashboard.refresh()} />

      <main className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <div className="grid min-w-0 gap-6 lg:grid-cols-[224px_minmax(0,1fr)] lg:gap-8">
          <SidebarNav
            currentView={currentView}
            onNavigate={setCurrentView}
            modelPullActive={pullActive}
            datasetPreparing={Boolean(dashboard.preparingDataset)}
            evaluationRunning={dashboard.runningEvaluation}
            resultAvailable={Boolean(dashboard.result)}
          />

          <div className="min-w-0">
            <section className="mb-6">
              <p className="mb-2 text-[11px] font-semibold tracking-[0.14em] text-primary uppercase">{copy.eyebrow}</p>
              <h1 className="text-3xl font-semibold tracking-[-0.04em] text-ink sm:text-4xl">{copy.title}</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted sm:text-base">{copy.description}</p>
            </section>

            <div hidden={currentView !== "overview"} className="space-y-5">
              <MetricStrip
                health={dashboard.health}
                ollamaState={ollamaState}
                datasetCount={dashboard.datasets.length}
                preparedCount={dashboard.datasets.filter((dataset) => dataset.prepared).length}
                latestScore={dashboard.result ? formatScore(dashboard.result.average_score) : "—"}
              />
              <OverviewPanel onNavigate={setCurrentView} />
            </div>

            <div hidden={currentView !== "evaluation"}>
              <EvaluationForm
                datasets={dashboard.datasets}
                modelOptions={dashboard.ollama?.model_options || []}
                model={model}
                baseUrl={baseUrl}
                running={dashboard.runningEvaluation}
                preparing={Boolean(dashboard.preparingDataset)}
                onModelChange={(nextModel) => {
                  setDismissedDownloadModel(null);
                  setModel(nextModel);
                }}
                onBaseUrlCommit={setBaseUrl}
                onManageAssets={() => setCurrentView("assets")}
                onSubmit={(request) => {
                  setCurrentView("results");
                  void dashboard.run(request);
                }}
              />
            </div>

            <div hidden={currentView !== "assets"} className="space-y-5">
              <OllamaPanel
                status={dashboard.ollama}
                loading={dashboard.refreshing}
                error={dashboard.ollamaError}
                modelOption={selectedModelOption}
                pullTask={dashboard.modelPullTask}
                pullError={dashboard.modelPullError}
                downloadDismissed={dismissedDownloadModel === model}
                onDownload={(targetModel) => void dashboard.startModelPull(targetModel)}
                onCancel={(targetModel) => void dashboard.cancelModelPull(targetModel)}
                onDecline={() => {
                  const installedModel = dashboard.ollama?.model_options.find((option) => option.installed);
                  if (installedModel) {
                    setDismissedDownloadModel(null);
                    setModel(installedModel.name);
                  } else {
                    setDismissedDownloadModel(model);
                  }
                }}
              />
              <DatasetTable
                datasets={dashboard.datasets}
                preparingDataset={dashboard.preparingDataset}
                error={dashboard.datasetError}
                onPrepare={(dataset) => void dashboard.prepare(dataset)}
              />
            </div>

            <div hidden={currentView !== "results"}>
              <ResultPanel
                result={dashboard.result}
                running={dashboard.runningEvaluation}
                error={dashboard.evaluationError}
              />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
