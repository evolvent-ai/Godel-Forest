"""Manager-only final submission for the best completed official attempt."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from godel_forest.contract import WorkspaceContract

from .leaderboard import AttemptSummary, best_completed_attempt, read_budget_state
from .locks import file_lock


@dataclass
class FinalizeResult:
    submitted: bool
    attempt_id: str | None
    reason: str
    return_code: int | None = None


Runner = Callable[..., Any]


@dataclass
class BestAttemptFinalizer:
    """Select the highest completed attempt and invoke ``final_submit.sh`` once."""

    contract: WorkspaceContract
    lock_path: Path
    runner: Runner = field(default=subprocess.run)

    def select_best(self) -> AttemptSummary | None:
        with file_lock(self.lock_path):
            state = read_budget_state(self.contract.budget_state_path)
            return best_completed_attempt(state)

    def submit_best(self, env: dict | None = None) -> FinalizeResult:
        """Submit the best completed attempt under the shared budget lock."""
        with file_lock(self.lock_path):
            state = read_budget_state(self.contract.budget_state_path)
            best = best_completed_attempt(state)
            if best is None:
                return FinalizeResult(False, None, "no_completed_attempt")
            if self.contract.final_selection_exists():
                return FinalizeResult(False, best.attempt_id, "already_submitted")
            result = self.runner(
                ["bash", str(self.contract.script("final_submit.sh")), best.attempt_id],
                cwd=str(self.contract.work_dir),
                env=env,
                capture_output=True,
                text=True,
            )
            rc = int(getattr(result, "returncode", 0))
            return FinalizeResult(
                submitted=rc == 0,
                attempt_id=best.attempt_id,
                reason="submitted" if rc == 0 else "submit_failed",
                return_code=rc,
            )
