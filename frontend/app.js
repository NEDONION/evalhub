const datasetsEl = document.querySelector("#datasets");
const healthEl = document.querySelector("#health");
const outputEl = document.querySelector("#output");
const resultSummaryEl = document.querySelector("#resultSummary");
const refreshBtn = document.querySelector("#refreshBtn");
const prepareBtn = document.querySelector("#prepareBtn");
const runForm = document.querySelector("#runForm");
const serviceMetricEl = document.querySelector("#serviceMetric");
const ollamaMetricEl = document.querySelector("#ollamaMetric");
const datasetMetricEl = document.querySelector("#datasetMetric");
const preparedMetricEl = document.querySelector("#preparedMetric");
const scoreMetricEl = document.querySelector("#scoreMetric");
const ollamaStatusEl = document.querySelector("#ollamaStatus");
const ollamaCommandEl = document.querySelector("#ollamaCommand");
const ollamaBaseUrlEl = document.querySelector("#ollamaBaseUrl");
const ollamaModelEl = document.querySelector("#ollamaModel");
const ollamaModelsEl = document.querySelector("#ollamaModels");
const ollamaMessageEl = document.querySelector("#ollamaMessage");
const limitFieldEl = document.querySelector("#limitField");
const limitInputEl = document.querySelector("#limitInput");
const modelInputEl = document.querySelector("#modelInput");
const baseUrlInputEl = document.querySelector("#baseUrlInput");

const taskTypeLabels = {
  math_reasoning: "数学推理",
  multiple_choice: "多选问答",
};

const metricLabels = {
  numeric_exact_match: "数值精确匹配",
  choice_letter: "选项匹配",
  exact_match: "精确匹配",
};

const adapterLabels = {
  ollama: "Ollama 本地模型",
  oracle: "Oracle 管线自检",
};

const fallbackModelOptions = [
  {
    name: "qwen2.5:0.5b",
    label: "Qwen2.5 0.5B",
    description: "默认轻量模型，适合快速验证中文和数学任务。",
    installed: false,
  },
  {
    name: "qwen2.5:1.5b",
    label: "Qwen2.5 1.5B",
    description: "轻量中文能力更好，适合本地评测入门。",
    installed: false,
  },
  {
    name: "llama3.2:1b",
    label: "Llama 3.2 1B",
    description: "轻量英文通用模型，适合低资源机器试跑。",
    installed: false,
  },
  {
    name: "llama3.2:3b",
    label: "Llama 3.2 3B",
    description: "通用能力更强，本地运行成本中等。",
    installed: false,
  },
  {
    name: "deepseek-r1:1.5b",
    label: "DeepSeek R1 1.5B",
    description: "轻量推理模型，适合观察推理题表现。",
    installed: false,
  },
  {
    name: "phi3:mini",
    label: "Phi-3 Mini",
    description: "小型通用模型，适合快速本地实验。",
    installed: false,
  },
];

function setOutput(value) {
  outputEl.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json();
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || `请求失败：${response.status}`);
  }
  return body;
}

async function refresh() {
  await Promise.all([refreshServiceAndDatasets(), refreshOllama()]);
}

async function refreshServiceAndDatasets() {
  try {
    await fetchJson("/api/health");
    healthEl.textContent = "服务在线";
    healthEl.className = "status ok";
    serviceMetricEl.textContent = "在线";

    const data = await fetchJson("/api/datasets");
    renderDatasets(data.datasets);
    datasetMetricEl.textContent = String(data.datasets.length);
    preparedMetricEl.textContent = String(data.datasets.filter((dataset) => dataset.prepared).length);
  } catch (error) {
    healthEl.textContent = "服务异常";
    healthEl.className = "status error";
    serviceMetricEl.textContent = "异常";
    setOutput(error.message);
  }
}

async function refreshOllama() {
  const model = encodeURIComponent(modelInputEl.value || "qwen2.5:0.5b");
  const baseUrl = encodeURIComponent(baseUrlInputEl.value || "http://127.0.0.1:11434");
  try {
    const status = await fetchJson(`/api/ollama/status?model=${model}&base_url=${baseUrl}`);
    renderOllama(status);
  } catch (error) {
    ollamaMetricEl.textContent = "异常";
    ollamaStatusEl.textContent = "检测失败";
    ollamaStatusEl.className = "status error";
    ollamaMessageEl.textContent = error.message;
    ollamaMessageEl.className = "notice error";
  }
}

function renderOllama(status) {
  renderModelOptions(status.model_options || fallbackModelOptions, status.model);
  ollamaCommandEl.textContent = status.command || "未检测到";
  ollamaBaseUrlEl.textContent = status.base_url;
  ollamaModelEl.textContent = status.model;
  ollamaModelsEl.textContent = String(status.models.length);
  ollamaMessageEl.textContent = status.message;

  if (!status.installed) {
    ollamaMetricEl.textContent = "未安装";
    ollamaStatusEl.textContent = "未安装";
    ollamaStatusEl.className = "status error";
    ollamaMessageEl.className = "notice error";
    return;
  }

  if (!status.running) {
    ollamaMetricEl.textContent = "未启动";
    ollamaStatusEl.textContent = "未启动";
    ollamaStatusEl.className = "status warning";
    ollamaMessageEl.className = "notice warning";
    return;
  }

  if (!status.model_present) {
    ollamaMetricEl.textContent = "缺模型";
    ollamaStatusEl.textContent = "模型未下载";
    ollamaStatusEl.className = "status warning";
    ollamaMessageEl.className = "notice warning";
    return;
  }

  ollamaMetricEl.textContent = "已就绪";
  ollamaStatusEl.textContent = "已就绪";
  ollamaStatusEl.className = "status ok";
  ollamaMessageEl.className = "notice ok";
}

function renderModelOptions(options, selectedModel) {
  const previousValue = modelInputEl.value || selectedModel || "qwen2.5:0.5b";
  modelInputEl.innerHTML = "";

  for (const option of options) {
    const optionEl = document.createElement("option");
    optionEl.value = option.name;
    optionEl.textContent = `${option.label || option.name} · ${option.installed ? "已安装" : "未下载"} · ${option.name}`;
    optionEl.title = option.description || "";
    modelInputEl.appendChild(optionEl);
  }

  const hasPrevious = options.some((option) => option.name === previousValue);
  if (hasPrevious) {
    modelInputEl.value = previousValue;
  } else if (options.length > 0) {
    modelInputEl.value = options[0].name;
  }
}

function renderDatasets(datasets) {
  datasetsEl.innerHTML = "";
  for (const dataset of datasets) {
    const item = document.createElement("article");
    item.className = "datasetItem";
    item.innerHTML = `
      <div class="assetTitle">
        <h3>${dataset.display_name}</h3>
        <span class="badge ${dataset.prepared ? "ready" : ""}">
          ${dataset.prepared ? "已缓存" : "未缓存"}
        </span>
      </div>
      <p>${dataset.description}</p>
      <dl>
        <dt>评测指标</dt>
        <dd>${metricLabels[dataset.evaluator_type] || dataset.evaluator_type}</dd>
        <dt>任务类型</dt>
        <dd>${taskTypeLabels[dataset.task_type] || dataset.task_type}</dd>
        <dt>样本状态</dt>
        <dd>${dataset.sample_count === null ? "未统计" : `${dataset.sample_count} 条`}</dd>
        <dt>本地路径</dt>
        <dd>${dataset.local_path}</dd>
        <dt>数据来源</dt>
        <dd><a href="${dataset.homepage}" target="_blank" rel="noreferrer">${dataset.homepage}</a></dd>
      </dl>
    `;
    datasetsEl.appendChild(item);
  }
}

function formPayload() {
  const formData = new FormData(runForm);
  const sampleMode = formData.get("sample_mode");
  const payload = {
    dataset: formData.get("dataset"),
    subject: formData.get("subject"),
    adapter: formData.get("adapter"),
    model: formData.get("model"),
    base_url: formData.get("base_url"),
    sample_mode: sampleMode,
  };

  if (sampleMode === "custom") {
    payload.limit = Number(formData.get("limit") || 20);
  }

  return payload;
}

function selectedSampleModeLabel() {
  const checked = runForm.querySelector("input[name='sample_mode']:checked");
  if (!checked) {
    return "全部样本";
  }
  if (checked.value === "quick") {
    return "快速试跑 5 条";
  }
  if (checked.value === "custom") {
    return `自定义 ${limitInputEl.value || 20} 条`;
  }
  return "全部样本";
}

function syncSampleMode() {
  const checked = runForm.querySelector("input[name='sample_mode']:checked");
  limitFieldEl.classList.toggle("hidden", !checked || checked.value !== "custom");
}

prepareBtn.addEventListener("click", async () => {
  const payload = formPayload();
  prepareBtn.disabled = true;
  setOutput(`正在缓存 ${payload.dataset}，首次下载可能需要一些时间。`);
  try {
    const result = await fetchJson("/api/datasets/prepare", {
      method: "POST",
      body: JSON.stringify({ dataset: payload.dataset }),
    });
    setOutput(result);
    await refresh();
  } catch (error) {
    setOutput(error.message);
  } finally {
    prepareBtn.disabled = false;
  }
});

runForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formPayload();
  setOutput(
    `正在执行 ${payload.dataset}，范围：${selectedSampleModeLabel()}，适配器：${
      adapterLabels[payload.adapter] || payload.adapter
    }，模型：${payload.model}。`
  );
  try {
    const result = await fetchJson("/api/evaluations/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderResult(result.result);
    await refresh();
  } catch (error) {
    setOutput(error.message);
  }
});

runForm.addEventListener("change", (event) => {
  if (event.target.name === "sample_mode") {
    syncSampleMode();
  }
  if (event.target.name === "model" || event.target.name === "base_url") {
    refreshOllama();
  }
});

refreshBtn.addEventListener("click", refresh);
syncSampleMode();
refresh();

function renderResult(result) {
  scoreMetricEl.textContent = `${Number(result.average_score || 0).toFixed(4)}`;
  resultSummaryEl.className = "resultSummary";
  resultSummaryEl.innerHTML = `
    <div class="summaryCell">
      <span>任务状态</span>
      <strong>${translateStatus(result.status)}</strong>
    </div>
    <div class="summaryCell">
      <span>Benchmark</span>
      <strong>${result.benchmark}</strong>
    </div>
    <div class="summaryCell">
      <span>模型</span>
      <strong>${result.model}</strong>
    </div>
    <div class="summaryCell">
      <span>样本通过</span>
      <strong>${result.passed_samples}/${result.total_samples}</strong>
    </div>
    <div class="summaryCell">
      <span>平均分</span>
      <strong>${Number(result.average_score || 0).toFixed(4)}</strong>
    </div>
  `;
  setOutput(result);
}

function translateStatus(status) {
  const labels = {
    pending: "等待中",
    running: "运行中",
    success: "成功",
    failed: "失败",
    canceled: "已取消",
  };
  return labels[status] || status;
}
