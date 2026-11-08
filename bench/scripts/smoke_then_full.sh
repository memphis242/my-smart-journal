#!/usr/bin/env bash
# Smoke-test one item/one task against the live endpoint, then run the full synthetic suite.
# Usage: bench/scripts/smoke_then_full.sh <label> <expected-server-model-substring>
set -euo pipefail
label="$1"; expect="$2"
py=/workspace/build/my-smart-journal-phase0/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/../.."
"$py" -m msj_bench llm --set synthetic --limit 1 --tasks cleanup --backend runpod \
  --expect-model "$expect" --label "smoke-$label"
echo "smoke ok: $label"
"$py" -m msj_bench llm --set synthetic --tasks cleanup,summarize,extract,review --backend runpod \
  --expect-model "$expect" --label "$label"
