#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DGB_ROOT="${1:-${RSIBENCH_DATA_REPO_ROOT:-/data/RSIBench-Data}}"
PYTHON_BIN="$DGB_ROOT/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "RSIBench-Data Python not found: $PYTHON_BIN" >&2
  echo "Create RSIBench-Data's .venv before installing godel_forest." >&2
  exit 2
fi
if [ ! -f "$DGB_ROOT/tools/run_session_in_docker.sh" ]; then
  echo "Docker session wrapper not found: $DGB_ROOT/tools/run_session_in_docker.sh" >&2
  exit 2
fi

"$PYTHON_BIN" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --no-build-isolation \
  --no-deps \
  --upgrade \
  --force-reinstall \
  "$AGENT_ROOT"
"$PYTHON_BIN" - <<'PY'
from importlib.resources import files
import godel_forest.contract

root = files("godel_forest.workspace_assets")
required_assets = (
    "GODEL_FOREST_FLOW.md",
    "DATA_PLAN.md",
    "ATTEMPT_REVIEW.md",
    ".claude/skills/human-research/SKILL.md",
    ".claude/skills/candidate-reflection/SKILL.md",
    ".claude/skills/diagnostic-eval-plan/SKILL.md",
    ".claude/skills/attempt-review/SKILL.md",
)
for relative_path in required_assets:
    asset = root.joinpath(*relative_path.split("/"))
    if not asset.is_file():
        raise SystemExit(f"installed package is missing workspace asset: {asset}")
    print(f"godel_forest_asset={asset}")
print(f"godel_forest_module={godel_forest.contract.__file__}")
PY
