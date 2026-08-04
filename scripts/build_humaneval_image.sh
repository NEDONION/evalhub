#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
context="${repo_root}/docker/hexagon-humaneval"
verifier_identity="$(
  PYTHONPATH="${repo_root}/src" "${repo_root}/.venv/bin/python" -c \
    'from pathlib import Path; import sys; from evalhub.benchmarks.humaneval import humaneval_verifier_identity; print(humaneval_verifier_identity(Path(sys.argv[1])))' \
    "${context}"
)"

docker build \
  --label "io.evalhub.humaneval.verifier.sha256=${verifier_identity}" \
  --tag evalhub-humaneval:1.0.0 \
  "${context}"
