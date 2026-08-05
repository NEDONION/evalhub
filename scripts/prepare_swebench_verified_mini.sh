#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_ROOT="${PROJECT_ROOT}/.runtime/swebench-verified-mini"
SOURCE_ROOT="${RUNTIME_ROOT}/source"
HARNESS_PYTHON="${RUNTIME_ROOT}/venv/bin/python"
HARNESS_REVISION="f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
RUN_ID="evalhub-swebench-verified-mini-gold"
REPORT_PATH="${PROJECT_ROOT}/gold.${RUN_ID}.json"
MARKER_PATH="${RUNTIME_ROOT}/gold-validation.json"

mkdir -p "${RUNTIME_ROOT}"
if [[ ! -d "${SOURCE_ROOT}/.git" ]]; then
  git clone https://github.com/SWE-bench/SWE-bench.git "${SOURCE_ROOT}"
fi
git -C "${SOURCE_ROOT}" fetch --depth 1 origin "${HARNESS_REVISION}"
git -C "${SOURCE_ROOT}" checkout --detach "${HARNESS_REVISION}"

if [[ ! -x "${HARNESS_PYTHON}" ]]; then
  python3 -m venv "${RUNTIME_ROOT}/venv"
fi
"${HARNESS_PYTHON}" -m pip install -e "${SOURCE_ROOT}"

cd "${PROJECT_ROOT}"
"${HARNESS_PYTHON}" -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path gold \
  --instance_ids \
    psf__requests-2931 \
    psf__requests-6028 \
    pydata__xarray-2905 \
    pydata__xarray-7229 \
    pytest-dev__pytest-7324 \
    pytest-dev__pytest-10356 \
  --max_workers 2 \
  --run_id "${RUN_ID}" \
  --cache_level env \
  --clean True

"${PROJECT_ROOT}/.venv/bin/python" -m evalhub.benchmarks.swebench_verified_mini \
  record-gold \
  --report "${REPORT_PATH}" \
  --marker "${MARKER_PATH}"
mv "${REPORT_PATH}" "${RUNTIME_ROOT}/last-gold-report.json"
echo "SWE-bench Verified Mini gold validation passed: 6/6"
