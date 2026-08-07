import { useMemo, useState } from "react";

import { DatasetTable } from "./components/dashboard/DatasetTable";
import { EvaluationForm } from "./components/dashboard/EvaluationForm";
import { EvaluationTaskPanel } from "./components/dashboard/EvaluationTaskPanel";
import { Header } from "./components/dashboard/Header";
import { MetricStrip } from "./components/dashboard/MetricStrip";
import { ModelPerformancePanel } from "./components/dashboard/ModelPerformancePanel";
import { OllamaPanel } from "./components/dashboard/OllamaPanel";
import { OverviewPanel } from "./components/dashboard/OverviewPanel";
import { SidebarNav, type WorkspaceView } from "./components/dashboard/SidebarNav";
import { useEvalHub } from "./hooks/useEvalHub";
import { formatScore } from "./lib/evaluation";

const DEFAULT_MODEL = "granite4.1:3b";
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
    eyebrow: "Evaluation jobs",
    title: "评测任务",
    description: "先追踪执行状态与资源占用，选择任务后再查看完整评测结果。",
  },
  performance: {
    eyebrow: "Model performance",
    title: "模型成绩",
    description: "按评测类型与相同 Benchmark 或 Suite，分别比较历史最佳、最近表现与成绩趋势。",
  },
};

/**
 * 渲染 EvalHub 工作区，并持有跨目录共享的模型、服务地址与当前视图状态。
 * 任务中心与资产操作由同一 Hook 驱动，目录切换不会中断下载、排队任务或活动评测。
 *
 * @returns 包含概览、评测配置、资产管理和持久化任务中心的完整页面。
 */
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

  const latestScore = useMemo(() => {
    const latestCompleted = dashboard.tasks.find((task) => task.result_summary !== null);
    return latestCompleted ? formatScore(latestCompleted.result_summary!.average_score) : "—";
  }, [dashboard.tasks]);
  const pullActive = Boolean(
    dashboard.modelPullTask && ["pending", "pulling", "verifying"].includes(dashboard.modelPullTask.status),
  );
  const copy = pageCopy[currentView];

  return (
    <div className="min-h-screen bg-page text-ink">
      <Header health={dashboard.health} refreshing={dashboard.refreshing} onRefresh={() => void dashboard.refresh()} />

      <main className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <div className="grid min-w-0 gap-6 lg:grid-cols-[272px_minmax(0,1fr)] lg:gap-8">
          <SidebarNav
            currentView={currentView}
            onNavigate={setCurrentView}
            modelPullActive={pullActive}
            datasetPreparing={Boolean(dashboard.preparingDataset)}
            evaluationRunning={dashboard.hasActiveTask}
            resultAvailable={dashboard.tasks.some((task) => task.result_summary !== null)}
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
                latestScore={latestScore}
              />
              <OverviewPanel onNavigate={setCurrentView} />
            </div>

            <div hidden={currentView !== "evaluation"}>
              <EvaluationForm
                agents={dashboard.agents}
                datasets={dashboard.datasets}
                benchmarks={dashboard.benchmarks}
                suites={dashboard.suites}
                modelOptions={dashboard.ollama?.model_options || []}
                model={model}
                baseUrl={baseUrl}
                running={dashboard.creatingEvaluation}
                preparing={Boolean(dashboard.preparingDataset)}
                onModelChange={(nextModel) => {
                  setDismissedDownloadModel(null);
                  setModel(nextModel);
                }}
                onBaseUrlCommit={setBaseUrl}
                onManageAssets={() => setCurrentView("assets")}
                onPrepare={(dataset) => void dashboard.prepare(dataset)}
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
                notice={dashboard.datasetNotice}
                onPrepare={(dataset, force) => void dashboard.prepare(dataset, force)}
              />
            </div>

            <div hidden={currentView !== "results"}>
              <EvaluationTaskPanel
                tasks={dashboard.tasks}
                selectedTaskId={dashboard.selectedTaskId}
                selectedTask={dashboard.selectedTask}
                error={dashboard.taskError}
                onSelect={dashboard.selectTask}
                onCancel={(taskId) => void dashboard.cancelTask(taskId)}
                retryingNodeId={dashboard.retryingNodeId}
                onRetryNode={dashboard.retryNode}
              />
            </div>

            <div hidden={currentView !== "performance"}>
              <ModelPerformancePanel
                evaluationType={dashboard.performanceEvaluationType}
                report={dashboard.modelPerformance}
                loading={dashboard.modelPerformanceLoading}
                error={dashboard.modelPerformanceError}
                onScopeChange={(scope) => void dashboard.selectPerformanceScope(scope)}
                onEvaluationTypeChange={(evaluationType) =>
                  void dashboard.selectPerformanceEvaluationType(evaluationType)
                }
                onStartEvaluation={() => setCurrentView("evaluation")}
              />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
