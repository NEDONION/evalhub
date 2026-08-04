#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
docker build --tag evalhub-humaneval:1.0.0 "${script_dir}/../docker/hexagon-humaneval"
