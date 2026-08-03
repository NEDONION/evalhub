import { useCallback, useEffect, useState } from "react";

import { getDatasets, getHealth, getOllamaStatus, prepareDataset, runEvaluation } from "../lib/api";
import type { Dataset, DatasetName, EvaluationRequest, EvaluationResult, OllamaStatus } from "../types";

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
  const [datasetError, setDatasetError] = useState<string | null>(null);
  const [ollamaError, setOllamaError] = useState<string | null>(null);
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

  const prepare = useCallback(
    async (dataset: DatasetName) => {
      setPreparingDataset(dataset);
      setDatasetError(null);
      try {
        await prepareDataset(dataset);
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
    refreshing,
    preparingDataset,
    runningEvaluation,
    datasetError,
    ollamaError,
    evaluationError,
    refresh,
    prepare,
    run,
  };
}
