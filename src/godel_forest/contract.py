"""Workspace contract adapter for RSIBench-Data.

This module is the *only* place that knows how the RSIBench-Data harness talks to
an external data-generation agent. The harness invokes us through the
``DATA_AGENT_COMMAND`` seam in ``runner/run_session.sh``; it does not import any
of our code. The coupling surface is purely:

  * environment variables exported to the subprocess (``RSIBENCH_DATA_*``,
    ``ANTHROPIC_*``, ``TARGET_MODEL`` ...)
  * the four generated workspace scripts (``timer.sh``, ``budget_status.sh``,
    ``run_attempt.sh``, ``final_submit.sh``)
  * the JSON state files (``budget_state.json``, ``final_selection.json``)

We deliberately keep this adapter free of any RSIBench-Data import so the two
repos stay decoupled and can evolve independently as long as this contract
holds.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Scripts the harness generates into the workspace. We only *call* these; we
# never read or rewrite them (doing so would trip the tamper audit).
REQUIRED_WORKSPACE_SCRIPTS = (
    "timer.sh",
    "budget_status.sh",
    "run_attempt.sh",
    "final_submit.sh",
)

# Workspace instruction files the agent prompt tells Claude to read. Their
# presence is part of the contract; their content is owned by RSIBench-Data.
EXPECTED_INSTRUCTION_FILES = (
    "RUN_CONTRACT.md",
    "AGENT_RULES.md",
    "DATA_GENERATION.md",
    "MINI_SWE_DATA_FORMAT.md",
    "EVAL_ATTEMPT_LOOP.md",
)


class ContractError(RuntimeError):
    """Raised when the harness environment does not match the expected contract.

    We fail fast with a precise message instead of letting Claude start and die
    halfway through, which would waste wall-time and Tinker budget.
    """


@dataclass(frozen=True)
class WorkspaceContract:
    """Resolved, validated view of the harness-provided environment.

    Attributes mirror exactly what ``run_session.sh`` exports to the
    ``DATA_AGENT_COMMAND`` subprocess. ``model`` falls back to
    ``ANTHROPIC_MODEL`` because ``DATA_AGENT_MODEL`` is a *local* shell variable
    in the harness and is not exported to us.
    """

    work_dir: Path
    repo_root: Path
    prompt_path: Path
    budget_state_path: Path
    model: str
    min_remaining_sec: int

    @property
    def final_selection_path(self) -> Path:
        return self.work_dir / "final_selection.json"

    def script(self, name: str) -> Path:
        return self.work_dir / name

    def final_selection_exists(self) -> bool:
        return self.final_selection_path.is_file()

    def remaining_wall_time(self) -> int:
        """Seconds left on the wall-time budget, computed like the harness.

        Mirrors ``remaining_wall_time`` in run_session.sh: budget minus elapsed
        since ``started_at``, floored at 0. Returns 0 if state is unreadable so
        callers treat an unknown budget as exhausted rather than infinite.
        """
        state = self._read_budget_state()
        if state is None:
            return 0
        now = int(time.time())
        started = int(state.get("started_at", now))
        budget = int(state.get("budget", {}).get("wall_time_budget_sec", 0))
        return max(0, budget - max(0, now - started))

    def remaining_cost(self) -> float:
        """Remaining Tinker USD budget, mirroring the harness ``remaining_cost``."""
        state = self._read_budget_state()
        if state is None:
            return 0.0
        return float(state.get("remaining_tinker_cost_usd", 0.0))

    def _read_budget_state(self) -> dict | None:
        try:
            return json.loads(self.budget_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _find_budget_state(work_dir: Path) -> Path:
    """Locate budget_state.json.

    The harness keeps it at ``<session>/runner_state/budget_state.json`` while
    the workspace is ``<session>/agent_workspace``. We do not get the path
    exported directly, so derive it from the workspace's parent session dir.
    """
    candidate = work_dir.parent / "runner_state" / "budget_state.json"
    return candidate


def load_contract(env: dict | None = None) -> WorkspaceContract:
    """Read and validate the harness environment, or raise ``ContractError``.

    This is the fail-fast gate: it confirms the workspace, prompt, required
    scripts, and Anthropic configuration are all present and coherent before we
    spend any budget launching Claude.
    """
    env = os.environ if env is None else env

    work_dir_raw = env.get("RSIBENCH_DATA_WORK_DIR")
    if not work_dir_raw:
        raise ContractError(
            "RSIBENCH_DATA_WORK_DIR is not set; "
            "this process must be launched by RSIBench-Data's run_session.sh via "
            "DATA_AGENT_COMMAND."
        )
    work_dir = Path(work_dir_raw)
    if not work_dir.is_dir():
        raise ContractError(f"workspace directory does not exist: {work_dir}")

    repo_root_raw = env.get("RSIBENCH_DATA_REPO_ROOT")
    repo_root = Path(repo_root_raw or "")
    if not repo_root or not repo_root.is_dir():
        raise ContractError(
            "RSIBENCH_DATA_REPO_ROOT "
            f"missing or not a directory: {repo_root!r}"
        )

    prompt_path_raw = env.get("RSIBENCH_DATA_PROMPT_PATH")
    prompt_path = Path(prompt_path_raw or "")
    if not prompt_path or not prompt_path.is_file():
        raise ContractError(
            "RSIBENCH_DATA_PROMPT_PATH "
            f"missing or not a file: {prompt_path!r}"
        )

    missing_scripts = [
        name for name in REQUIRED_WORKSPACE_SCRIPTS if not (work_dir / name).is_file()
    ]
    if missing_scripts:
        raise ContractError(
            "workspace is missing required scripts: " + ", ".join(missing_scripts)
        )

    harness = env.get("DATA_AGENT_HARNESS", "claude-code").strip().lower()
    if harness == "codex":
        base_url = env.get("CODEX_BASE_URL")
        api_key = env.get("CODEX_API_KEY")
        if bool(base_url) != bool(api_key):
            raise ContractError(
                "CODEX_BASE_URL and CODEX_API_KEY must both be set for Codex."
            )
        model = env.get("DATA_AGENT_MODEL") or env.get("CODEX_MODEL")
    else:
        # Match current configure_claude_code_env: custom credentials require a
        # base URL and either an API key or an auth token. Auth token wins if both
        # are present.
        base_url = env.get("ANTHROPIC_BASE_URL")
        api_key = env.get("ANTHROPIC_API_KEY")
        auth_token = env.get("ANTHROPIC_AUTH_TOKEN")
        if (api_key or auth_token) and not base_url:
            raise ContractError(
                "ANTHROPIC_BASE_URL must be set when ANTHROPIC_API_KEY or "
                "ANTHROPIC_AUTH_TOKEN is configured."
            )
        if base_url and not (api_key or auth_token):
            raise ContractError(
                "set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN when "
                "ANTHROPIC_BASE_URL is configured."
            )
        model = env.get("DATA_AGENT_MODEL") or env.get("ANTHROPIC_MODEL")
    if not model:
        raise ContractError(
            "no agent model available: set DATA_AGENT_MODEL, CODEX_MODEL, or "
            "ANTHROPIC_MODEL."
        )

    # MIN_REMAINING_SEC is also local to the harness; default to its 1800.
    try:
        min_remaining_sec = int(env.get("MIN_REMAINING_SEC", "1800"))
    except ValueError:
        min_remaining_sec = 1800

    return WorkspaceContract(
        work_dir=work_dir,
        repo_root=repo_root,
        prompt_path=prompt_path,
        budget_state_path=_find_budget_state(work_dir),
        model=model,
        min_remaining_sec=min_remaining_sec,
    )
