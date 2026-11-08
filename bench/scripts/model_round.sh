#!/usr/bin/env bash
# Smoke-test, then run every synthetic set (short: 4 tasks, long: summarize, compile) for one served model.
# Usage: bench/scripts/model_round.sh <label> <expected-server-model-substring>
set -euo pipefail
label="$1"; expect="$2"
py=/workspace/build/my-smart-journal-phase0/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/../.."
run() { "$py" -m msj_bench llm --backend runpod --expect-model "$expect" "$@" 2>&1 | tail -1; }
run --set synthetic --limit 1 --tasks cleanup --label "smoke-$label"
echo "smoke ok: $label"
run --set synthetic --tasks cleanup,summarize,extract,review --label "$label"
run --set synthetic-long --tasks summarize --label "$label-long"
run --set compile --tasks compile --label "$label-compile"
