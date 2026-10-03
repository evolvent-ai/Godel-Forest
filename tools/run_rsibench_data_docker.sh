#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DGB_ROOT="${RSIBENCH_DATA_REPO_ROOT:-/data/RSIBench-Data}"

if [ "${GODEL_FOREST_SKIP_INSTALL:-0}" != "1" ]; then
  bash "$SCRIPT_DIR/install_into_rsibench_data.sh" "$DGB_ROOT"
fi

if [ -f "$DGB_ROOT/.env" ]; then
  CONFIGURED_COMMAND="$(grep -E '^[[:space:]]*(export[[:space:]]+)?DATA_AGENT_COMMAND=' "$DGB_ROOT/.env" | tail -n 1 || true)"
  if [ -n "$CONFIGURED_COMMAND" ] && [[ "$CONFIGURED_COMMAND" != *"$DGB_ROOT/.venv/bin/python -m godel_forest"* ]]; then
    echo "RSIBench-Data .env overrides DATA_AGENT_COMMAND with a container-inaccessible command:" >&2
    echo "  $CONFIGURED_COMMAND" >&2
    echo "Set it to: $DGB_ROOT/.venv/bin/python -m godel_forest" >&2
    exit 2
  fi
fi

CONTAINER_ENV=(
  "DATA_AGENT_COMMAND=$DGB_ROOT/.venv/bin/python -m godel_forest"
)
for key in \
  GODEL_FOREST_MODE \
  GODEL_FOREST_AGENT_IDS \
  GODEL_FOREST_NUM_AGENTS \
  GODEL_FOREST_AGENT_MODEL \
  GODEL_FOREST_FINAL_BUFFER_SEC \
  GODEL_FOREST_AGENT_RESTART \
  GODEL_FOREST_AGENT_ERROR_GRACE_SEC \
  GODEL_FOREST_MONITOR_INTERVAL_SEC \
  GODEL_FOREST_MANAGER_MAX_ITERATIONS; do
  if [ -v "$key" ]; then
    CONTAINER_ENV+=("$key=${!key}")
  fi
done

export RSIBENCH_DOCKER_REPO_MOUNT="$DGB_ROOT"
cd "$DGB_ROOT"
exec bash tools/run_session_in_docker.sh \
  env "${CONTAINER_ENV[@]}" bash runner/run_session.sh
