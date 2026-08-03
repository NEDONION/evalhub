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

/**
 * 把未知异常收窄为可在本地控制台安全展示的稳定中文信息。
 *
 * @param error 请求、解析或业务边界抛出的未知值。
 * @returns `Error` 的消息或不泄露内部细节的兜底文案。
 */
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误";
}

/**
 * 管理 EvalHub 控制台的健康状态、数据集、Ollama 下载和评测结果。
 * Hook 会在模型或地址变化时刷新服务状态，恢复服务端下载任务，并以 500ms 间隔轮询活动下载；
 * 所有 Effect 都会在依赖变化或卸载时取消更新和清理计时器。
 *
 * @param model 当前希望探测或运行的 Ollama 模型标签。
 * @param baseUrl 当前 Ollama HTTP 服务根地址。
 * @returns 控制台渲染所需的状态，以及刷新、下载、取消、准备数据集和运行评测动作。
 */
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
