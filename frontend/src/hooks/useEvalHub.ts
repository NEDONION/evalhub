import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  cancelEvaluationTask,
  cancelModelPull as cancelModelPullRequest,
  createEvaluation,
  getBenchmarks,
  getDatasets,
  getEvaluationTask,
  getEvaluationTasks,
  getHealth,
  getModelPerformance,
  getModelPull,
  getOllamaStatus,
  getSuites,
  prepareDataset,
  retryEvaluationNode,
  startModelPull as startModelPullRequest,
} from "../lib/api";
import type {
  BenchmarkDefinition,
  BenchmarkSuite,
  Dataset,
  DatasetName,
  EvaluationRequest,
  EvaluationTaskDetail,
  EvaluationTaskSummary,
  ModelPerformanceResponse,
  OllamaPullTask,
  OllamaStatus,
} from "../types";

type HealthState = "loading" | "online" | "offline";

interface UseEvalHubResult {
  health: HealthState;
  datasets: Dataset[];
  benchmarks: BenchmarkDefinition[];
  suites: BenchmarkSuite[];
  ollama: OllamaStatus | null;
  tasks: EvaluationTaskSummary[];
  modelPerformance: ModelPerformanceResponse | null;
  selectedTaskId: string | null;
  selectedTask: EvaluationTaskDetail | null;
  modelPullTask: OllamaPullTask | null;
  refreshing: boolean;
  preparingDataset: DatasetName | null;
  creatingEvaluation: boolean;
  retryingNodeId: string | null;
  hasActiveTask: boolean;
  datasetError: string | null;
  datasetNotice: string | null;
  ollamaError: string | null;
  modelPullError: string | null;
  taskError: string | null;
  modelPerformanceError: string | null;
  modelPerformanceLoading: boolean;
  refresh: () => Promise<void>;
  startModelPull: (targetModel: string) => Promise<OllamaPullTask | null>;
  cancelModelPull: (targetModel: string) => Promise<OllamaPullTask | null>;
  prepare: (dataset: DatasetName, force?: boolean) => Promise<void>;
  run: (request: EvaluationRequest) => Promise<EvaluationTaskSummary | null>;
  selectTask: (taskId: string) => void;
  cancelTask: (taskId: string) => Promise<EvaluationTaskDetail | null>;
  retryNode: (taskId: string, nodeId: string) => Promise<EvaluationTaskDetail | null>;
  selectPerformanceScope: (scope: string) => Promise<void>;
}

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
 * 用服务端最新任务替换同标识旧记录，并把刚变化的任务置于列表顶部。
 *
 * @param tasks 当前任务摘要列表。
 * @param nextTask 创建或取消操作返回的最新任务状态。
 * @returns 不修改原数组的新任务列表。
 */
function replaceTask(
  tasks: EvaluationTaskSummary[],
  nextTask: EvaluationTaskSummary,
): EvaluationTaskSummary[] {
  const remaining = tasks.filter((task) => task.id !== nextTask.id);
  return [nextTask, ...remaining];
}

/**
 * 为已经形成分数的模型任务生成稳定签名，用于判断排行榜是否确实需要重载。
 *
 * @param tasks 当前服务端任务摘要。
 * @returns 与任务顺序无关、仅受模型成绩变化影响的签名。
 */
function modelScoreSignature(tasks: EvaluationTaskSummary[]): string {
  return tasks
    .filter((task) => task.evaluation_type !== "agent" && task.result_summary !== null)
    .map((task) => `${task.id}:${task.result_summary!.average_score}`)
    .sort()
    .join("|");
}

/**
 * 管理控制台健康状态、资产操作和持久化评测任务。
 * Hook 会恢复模型下载，串行轮询活动评测，并用请求版本防止慢响应覆盖用户的新选择。
 *
 * @param model 当前希望探测或运行的 Ollama 模型标签。
 * @param baseUrl 当前 Ollama HTTP 服务根地址。
 * @returns 页面渲染状态，以及刷新、下载、缓存、创建、选择和取消任务动作。
 */
export function useEvalHub(model: string, baseUrl: string): UseEvalHubResult {
  const [health, setHealth] = useState<HealthState>("loading");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [benchmarks, setBenchmarks] = useState<BenchmarkDefinition[]>([]);
  const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
  const [ollama, setOllama] = useState<OllamaStatus | null>(null);
  const [tasks, setTasks] = useState<EvaluationTaskSummary[]>([]);
  const [modelPerformance, setModelPerformance] = useState<ModelPerformanceResponse | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<EvaluationTaskDetail | null>(null);
  const [modelPullTask, setModelPullTask] = useState<OllamaPullTask | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [preparingDataset, setPreparingDataset] = useState<DatasetName | null>(null);
  const [creatingEvaluation, setCreatingEvaluation] = useState(false);
  const [retryingNodeId, setRetryingNodeId] = useState<string | null>(null);
  const [datasetError, setDatasetError] = useState<string | null>(null);
  const [datasetNotice, setDatasetNotice] = useState<string | null>(null);
  const [ollamaError, setOllamaError] = useState<string | null>(null);
  const [modelPullError, setModelPullError] = useState<string | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [modelPerformanceError, setModelPerformanceError] = useState<string | null>(null);
  const [modelPerformanceLoading, setModelPerformanceLoading] = useState(false);
  const [performanceRetryToken, setPerformanceRetryToken] = useState(0);
  const mountedRef = useRef(true);
  const latestTasksRef = useRef<EvaluationTaskSummary[]>([]);
  const selectedTaskIdRef = useRef<string | null>(selectedTaskId);
  const taskListRequestVersionRef = useRef(0);
  const detailRequestVersionRef = useRef(0);
  const requestedPerformanceScopeRef = useRef<string | null>(null);
  const modelScoreSignatureRef = useRef("");
  const performanceRequestVersionRef = useRef(0);
  selectedTaskIdRef.current = selectedTaskId;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      detailRequestVersionRef.current += 1;
      taskListRequestVersionRef.current += 1;
      performanceRequestVersionRef.current += 1;
    };
  }, []);

  const applyTaskList = useCallback((nextTasks: EvaluationTaskSummary[]) => {
    latestTasksRef.current = nextTasks;
    setTasks(nextTasks);
    setSelectedTaskId((current) => {
      if (current && nextTasks.some((task) => task.id === current)) return current;
      return nextTasks[0]?.id || null;
    });
  }, []);

  const loadModelPerformance = useCallback(async (scope?: string): Promise<boolean> => {
    requestedPerformanceScopeRef.current = scope || null;
    const taskSignature = modelScoreSignature(latestTasksRef.current);
    const requestVersion = performanceRequestVersionRef.current + 1;
    performanceRequestVersionRef.current = requestVersion;
    setModelPerformanceLoading(true);
    try {
      const report = await getModelPerformance(scope);
      if (!mountedRef.current || performanceRequestVersionRef.current !== requestVersion) return false;
      if (modelScoreSignature(latestTasksRef.current) !== taskSignature) return false;
      requestedPerformanceScopeRef.current = report.selected_scope?.key || scope || null;
      setModelPerformance(report);
      setModelPerformanceError(null);
      modelScoreSignatureRef.current = taskSignature;
      return true;
    } catch (error) {
      if (mountedRef.current && performanceRequestVersionRef.current === requestVersion) {
        setModelPerformanceError(errorMessage(error));
      }
      return false;
    } finally {
      if (mountedRef.current && performanceRequestVersionRef.current === requestVersion) {
        setModelPerformanceLoading(false);
      }
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const requestVersion = taskListRequestVersionRef.current + 1;
    taskListRequestVersionRef.current = requestVersion;
    const results = await Promise.allSettled([
      getHealth(),
      getDatasets(),
      getBenchmarks(),
      getSuites(),
      getOllamaStatus(model, baseUrl),
      getEvaluationTasks(),
    ]);
    if (!mountedRef.current) return;

    // 六类基础状态相互隔离，单个本地服务失败不会清空其他已经可用的工作区数据。
    const [
      healthResult,
      datasetsResult,
      benchmarksResult,
      suitesResult,
      ollamaResult,
      tasksResult,
    ] = results;
    setHealth(healthResult.status === "fulfilled" ? "online" : "offline");
    if (datasetsResult.status === "fulfilled") {
      setDatasets(datasetsResult.value.datasets);
      setDatasetError(null);
    } else {
      setDatasetError(errorMessage(datasetsResult.reason));
    }

    if (benchmarksResult.status === "fulfilled") {
      setBenchmarks(benchmarksResult.value.benchmarks);
    }
    if (suitesResult.status === "fulfilled") {
      setSuites(suitesResult.value.suites);
    }

    if (ollamaResult.status === "fulfilled") {
      setOllama(ollamaResult.value);
      setOllamaError(null);
    } else {
      setOllamaError(errorMessage(ollamaResult.reason));
    }

    // 写操作或新轮询会推进版本，较早的历史快照不得覆盖刚创建或取消的任务。
    if (taskListRequestVersionRef.current === requestVersion) {
      if (tasksResult.status === "fulfilled") {
        applyTaskList(tasksResult.value);
        setTaskError(null);
      } else {
        setTaskError(errorMessage(tasksResult.reason));
      }
      // 成绩读取严格排在任务快照之后，避免终态任务与旧榜单同时成为稳定页面状态。
      await loadModelPerformance(requestedPerformanceScopeRef.current || undefined);
    }
    setRefreshing(false);
  }, [applyTaskList, baseUrl, loadModelPerformance, model]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    let active = true;
    void getModelPull(model)
      .then((response) => {
        if (active && mountedRef.current) setModelPullTask(response.task);
      })
      .catch((error) => {
        if (active && mountedRef.current) setModelPullError(errorMessage(error));
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
          if (!active || !mountedRef.current) return;
          setModelPullTask(response.task);
          if (response.task?.status === "success") {
            setModelPullError(null);
            await refresh();
          } else if (response.task?.status === "failed") {
            setModelPullError(response.task.error || response.task.message);
          }
        })
        .catch((error) => {
          if (active && mountedRef.current) setModelPullError(errorMessage(error));
        });
    }, 500);
    return () => {
      active = false;
      window.clearTimeout(timeout);
    };
  }, [modelPullTask, refresh]);

  const loadSelectedTask = useCallback(async (taskId: string) => {
    const requestVersion = detailRequestVersionRef.current + 1;
    detailRequestVersionRef.current = requestVersion;
    try {
      const detail = await getEvaluationTask(taskId);
      if (
        !mountedRef.current ||
        selectedTaskIdRef.current !== taskId ||
        detailRequestVersionRef.current !== requestVersion
      ) {
        return null;
      }
      setSelectedTask(detail);
      setTaskError(null);
      return detail;
    } catch (error) {
      if (
        !mountedRef.current ||
        selectedTaskIdRef.current !== taskId ||
        detailRequestVersionRef.current !== requestVersion
      ) {
        return null;
      }
      setTaskError(errorMessage(error));
      return null;
    }
  }, []);

  useEffect(() => {
    if (!selectedTaskId) {
      detailRequestVersionRef.current += 1;
      setSelectedTask(null);
      return;
    }
    setSelectedTask((current) => (current?.id === selectedTaskId ? current : null));
    void loadSelectedTask(selectedTaskId);
  }, [loadSelectedTask, selectedTaskId]);

  const hasActiveTask = useMemo(
    () => tasks.some((task) => task.status === "pending" || task.status === "running"),
    [tasks],
  );

  useEffect(() => {
    const scoreSignature = modelScoreSignature(tasks);
    if (refreshing || scoreSignature === modelScoreSignatureRef.current) return undefined;
    let active = true;
    let retryId: number | undefined;

    // 成绩加载失败时保留未提交签名，并独立于任务轮询安排有限间隔重试。
    void loadModelPerformance(requestedPerformanceScopeRef.current || undefined).then((success) => {
      if (!success && active && mountedRef.current) {
        retryId = window.setTimeout(() => setPerformanceRetryToken((value) => value + 1), 1500);
      }
    });
    return () => {
      active = false;
      if (retryId !== undefined) window.clearTimeout(retryId);
    };
  }, [loadModelPerformance, performanceRetryToken, refreshing, tasks]);

  useEffect(() => {
    if (!hasActiveTask) return undefined;
    let active = true;
    let timeoutId: number | undefined;

    /** 完成一次列表和选中详情刷新后再安排下一次，避免慢请求重叠。 */
    async function pollTasks(): Promise<void> {
      const requestVersion = taskListRequestVersionRef.current + 1;
      taskListRequestVersionRef.current = requestVersion;
      try {
        const nextTasks = await getEvaluationTasks();
        if (!active || !mountedRef.current || taskListRequestVersionRef.current !== requestVersion) return;
        applyTaskList(nextTasks);
        setTaskError(null);
        if (selectedTaskId) await loadSelectedTask(selectedTaskId);
      } catch (error) {
        if (active && mountedRef.current && taskListRequestVersionRef.current === requestVersion) {
          setTaskError(errorMessage(error));
        }
      } finally {
        if (active && mountedRef.current) timeoutId = window.setTimeout(() => void pollTasks(), 1000);
      }
    }

    timeoutId = window.setTimeout(() => void pollTasks(), 1000);
    return () => {
      active = false;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [applyTaskList, hasActiveTask, loadSelectedTask, selectedTaskId]);

  const startModelPull = useCallback(
    async (targetModel: string) => {
      setModelPullError(null);
      try {
        const response = await startModelPullRequest(targetModel, baseUrl);
        if (!mountedRef.current) return null;
        setModelPullTask(response.task);
        return response.task;
      } catch (error) {
        if (mountedRef.current) setModelPullError(errorMessage(error));
        return null;
      }
    },
    [baseUrl],
  );

  const cancelModelPull = useCallback(async (targetModel: string) => {
    setModelPullError(null);
    try {
      const response = await cancelModelPullRequest(targetModel);
      if (!mountedRef.current) return null;
      setModelPullTask(response.task);
      return response.task;
    } catch (error) {
      if (mountedRef.current) setModelPullError(errorMessage(error));
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
        if (!mountedRef.current) return;
        setDatasetNotice(
          `${dataset.toUpperCase()} 已${response.operation === "updated" ? "更新" : "缓存"}，${response.sample_count.toLocaleString("zh-CN")} 条样本`,
        );
        await refresh();
      } catch (error) {
        if (mountedRef.current) setDatasetError(errorMessage(error));
      } finally {
        if (mountedRef.current) setPreparingDataset(null);
      }
    },
    [refresh],
  );

  const run = useCallback(async (request: EvaluationRequest) => {
    setCreatingEvaluation(true);
    setTaskError(null);
    try {
      const task = await createEvaluation(request);
      if (!mountedRef.current) return null;
      taskListRequestVersionRef.current += 1;
      setTasks((current) => replaceTask(current, task));
      setSelectedTaskId(task.id);
      return task;
    } catch (error) {
      if (mountedRef.current) setTaskError(errorMessage(error));
      return null;
    } finally {
      if (mountedRef.current) setCreatingEvaluation(false);
    }
  }, []);

  const cancelTask = useCallback(async (taskId: string) => {
    // 用户取消优先于已经在途的旧详情请求，先使旧响应失效再等待写操作确认。
    detailRequestVersionRef.current += 1;
    try {
      const task = await cancelEvaluationTask(taskId);
      if (!mountedRef.current) return null;
      taskListRequestVersionRef.current += 1;
      detailRequestVersionRef.current += 1;
      setTasks((current) => replaceTask(current, task));
      if (selectedTaskIdRef.current === taskId) setSelectedTask(task);
      setTaskError(null);
      return task;
    } catch (error) {
      if (mountedRef.current) setTaskError(errorMessage(error));
      return null;
    }
  }, []);

  const retryNode = useCallback(async (taskId: string, nodeId: string) => {
    setRetryingNodeId(nodeId);
    setTaskError(null);
    try {
      await retryEvaluationNode(taskId, nodeId);
      detailRequestVersionRef.current += 1;
      const task = await getEvaluationTask(taskId);
      if (!mountedRef.current) return null;
      taskListRequestVersionRef.current += 1;
      setTasks((current) => replaceTask(current, task));
      if (selectedTaskIdRef.current === taskId) setSelectedTask(task);
      return task;
    } catch (error) {
      if (mountedRef.current) setTaskError(errorMessage(error));
      return null;
    } finally {
      if (mountedRef.current) setRetryingNodeId(null);
    }
  }, []);

  return {
    health,
    datasets,
    benchmarks,
    suites,
    ollama,
    tasks,
    modelPerformance,
    selectedTaskId,
    selectedTask,
    modelPullTask,
    refreshing,
    preparingDataset,
    creatingEvaluation,
    retryingNodeId,
    hasActiveTask,
    datasetError,
    datasetNotice,
    ollamaError,
    modelPullError,
    taskError,
    modelPerformanceError,
    modelPerformanceLoading,
    refresh,
    startModelPull,
    cancelModelPull,
    prepare,
    run,
    selectTask: setSelectedTaskId,
    cancelTask,
    retryNode,
    selectPerformanceScope: async (scope) => {
      await loadModelPerformance(scope);
    },
  };
}
