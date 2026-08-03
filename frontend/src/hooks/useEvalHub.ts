import { useCallback, useEffect, useState } from "react";

import {
  cancelModelPull as cancelModelPullRequest,
  getDatasets,
  getHealth,
  getModelPull,
  getOllamaStatus,
  prepareDataset,
  runEvaluation,
  startModelPull as startModelPullRequest,
} from "../lib/api";
import type {
  Dataset,
  DatasetName,
  EvaluationRequest,
  EvaluationResult,
  OllamaPullTask,
  OllamaStatus,
} from "../types";

type HealthState = "loading" | "online" | "offline";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误";
}

export function useEvalHub(model: string, baseUrl: string) {
  const [health, setHealth] = useState<HealthState>("loading");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [ollama, setOllama] = useState<OllamaStatus | null>(null);
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [preparingDataset, setPreparingDataset] = useState<DatasetName | null>(null);
  const [runningEvaluation, setRunningEvaluation] = useState(false);
  const [modelPullTask, setModelPullTask] = useState<OllamaPullTask | null>(null);
  const [datasetError, setDatasetError] = useState<string | null>(null);
  const [datasetNotice, setDatasetNotice] = useState<string | null>(null);
  const [ollamaError, setOllamaError] = useState<string | null>(null);
  const [modelPullError, setModelPullError] = useState<string | null>(null);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const [healthResult, datasetsResult, ollamaResult] = await Promise.allSettled([
      getHealth(),
      getDatasets(),
      getOllamaStatus(model, baseUrl),
    ]);

    setHealth(healthResult.status === "fulfilled" ? "online" : "offline");

    if (datasetsResult.status === "fulfilled") {
      setDatasets(datasetsResult.value.datasets);
      setDatasetError(null);
    } else {
      setDatasetError(errorMessage(datasetsResult.reason));
    }

    if (ollamaResult.status === "fulfilled") {
      setOllama(ollamaResult.value);
      setOllamaError(null);
    } else {
      setOllamaError(errorMessage(ollamaResult.reason));
    }
    setRefreshing(false);
  }, [baseUrl, model]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    let active = true;
    void getModelPull(model)
      .then((response) => {
        if (active) setModelPullTask(response.task);
      })
      .catch((error) => {
        if (active) setModelPullError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [model]);

  useEffect(() => {
    if (!modelPullTask || !["pending", "pulling", "verifying"].includes(modelPullTask.status)) {
      return undefined;
    }
    let active = true;
    const timeout = window.setTimeout(() => {
      void getModelPull(modelPullTask.model)
        .then(async (response) => {
          if (!active) return;
          setModelPullTask(response.task);
          if (response.task?.status === "success") {
            setModelPullError(null);
            await refresh();
          } else if (response.task?.status === "failed") {
            setModelPullError(response.task.error || response.task.message);
          }
        })
        .catch((error) => {
          if (active) setModelPullError(errorMessage(error));
        });
    }, 500);
    return () => {
      active = false;
      window.clearTimeout(timeout);
    };
  }, [modelPullTask, refresh]);

  const startModelPull = useCallback(
    async (targetModel: string) => {
      setModelPullError(null);
      try {
        const response = await startModelPullRequest(targetModel, baseUrl);
        setModelPullTask(response.task);
        return response.task;
      } catch (error) {
        setModelPullError(errorMessage(error));
        return null;
      }
    },
    [baseUrl],
  );

  const cancelModelPull = useCallback(async (targetModel: string) => {
    setModelPullError(null);
    try {
      const response = await cancelModelPullRequest(targetModel);
      setModelPullTask(response.task);
      return response.task;
    } catch (error) {
      setModelPullError(errorMessage(error));
      return null;
    }
  }, []);

  const prepare = useCallback(
    async (dataset: DatasetName, force = false) => {
      setPreparingDataset(dataset);
      setDatasetError(null);
      setDatasetNotice(null);
      try {
        const response = await prepareDataset(dataset, force);
        setDatasetNotice(
          `${dataset.toUpperCase()} 已${response.operation === "updated" ? "更新" : "缓存"}，${response.sample_count.toLocaleString("zh-CN")} 条样本`,
        );
        await refresh();
      } catch (error) {
        setDatasetError(errorMessage(error));
      } finally {
        setPreparingDataset(null);
      }
    },
    [refresh],
  );

  const run = useCallback(async (request: EvaluationRequest) => {
    setRunningEvaluation(true);
    setEvaluationError(null);
    try {
      const nextResult = await runEvaluation(request);
      setResult(nextResult);
      return nextResult;
    } catch (error) {
      setEvaluationError(errorMessage(error));
      return null;
    } finally {
      setRunningEvaluation(false);
    }
  }, []);

  return {
    health,
    datasets,
    ollama,
    result,
    modelPullTask,
    refreshing,
    preparingDataset,
    runningEvaluation,
    datasetError,
    datasetNotice,
    ollamaError,
    modelPullError,
    evaluationError,
    refresh,
    startModelPull,
    cancelModelPull,
    prepare,
    run,
  };
}
