#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source "$HOME/venvs/chia-memscale/bin/activate"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO=0
: "${GEMINI_API_KEY:?Export GEMINI_API_KEY before running}"
mkdir -p results
timeout --signal=INT --kill-after=20s 22m python -u loop.py --backend native --experiments 4 "$@"
