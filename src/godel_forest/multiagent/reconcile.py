"""Best-effort mismatch detection before final submission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .leaderboard import read_budget_state
from .locks import file_lock


@dataclass(frozen=True)
class ReconcileReport:
    public_attempts: tuple[str, ...]
    budget_attempts: tuple[str, ...]
    missing_from_budget: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "public_attempts": list(self.public_attempts),
            "budget_attempts": list(self.budget_attempts),
            "missing_from_budget": list(self.missing_from_budget),
        }


def detect_mismatches(public_attempts_dir: Path, budget_state_path: Path, lock_path: Path) -> ReconcileReport:
    """Detect public attempt summaries missing from budget_state.

    This phase intentionally only detects mismatches. Automatic repair can be
    added once the reconstruction path has dedicated tests.
    """
    public_ids: set[str] = set()
    if public_attempts_dir.is_dir():
        for path in public_attempts_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            attempt_id = data.get("attempt_id")
            if isinstance(attempt_id, str):
                public_ids.add(attempt_id)
    with file_lock(lock_path):
        state = read_budget_state(budget_state_path)
    budget_ids = {
        item.get("attempt_id")
        for item in state.get("iterations", [])
        if isinstance(item, dict) and isinstance(item.get("attempt_id"), str)
    }
    missing = public_ids - budget_ids
    return ReconcileReport(
        public_attempts=tuple(sorted(public_ids)),
        budget_attempts=tuple(sorted(budget_ids)),
        missing_from_budget=tuple(sorted(missing)),
    )
