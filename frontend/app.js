const datasetsEl = document.querySelector("#datasets");
const healthEl = document.querySelector("#health");
const outputEl = document.querySelector("#output");
const resultSummaryEl = document.querySelector("#resultSummary");
const refreshBtn = document.querySelector("#refreshBtn");
const prepareBtn = document.querySelector("#prepareBtn");
const runForm = document.querySelector("#runForm");
const serviceMetricEl = document.querySelector("#serviceMetric");
const datasetMetricEl = document.querySelector("#datasetMetric");
const preparedMetricEl = document.querySelector("#preparedMetric");
const scoreMetricEl = document.querySelector("#scoreMetric");

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
  return {
    dataset: formData.get("dataset"),
    subject: formData.get("subject"),
    adapter: formData.get("adapter"),
    model: formData.get("model"),
    base_url: formData.get("base_url"),
    limit: Number(formData.get("limit") || 5),
  };
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
  setOutput(`正在执行 ${payload.dataset}，适配器：${adapterLabels[payload.adapter] || payload.adapter}，模型：${payload.model}。`);
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

refreshBtn.addEventListener("click", refresh);
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
