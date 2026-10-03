#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: bash continue_eval.sh <subset_attempt_id>" >&2
  exit 2
fi

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

exec "$PYTHON_BIN" -m godel_forest.continue_eval \
  --workspace "$WORKSPACE" \
  "$1"
