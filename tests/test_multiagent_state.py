"""Tests for shared active-attempt state management."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.multiagent.state import (
    active_attempts,
    prune_stale_active_attempts,
    set_attempt_admissions,
)


class TestMultiAgentState(unittest.TestCase):
    def test_admissions_and_stale_attempt_cleanup_use_shared_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "runner_state" / "budget_state.json"
            lock_path = root / "runner_state" / "budget_state.lock"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "budget": {"tinker_cost_budget_usd": 10.0},
                        "estimated_tinker_usd_used": 2.0,
                        "active_attempts": [
                            {
                                "attempt_id": "live",
                                "owner_id": "agent1",
                                "pid": os.getpid(),
                                "reserved_tinker_usd": 3.0,
                            },
                            {
                                "attempt_id": "stale",
                                "owner_id": "agent2",
                                "pid": 99999999,
                                "reserved_tinker_usd": 4.0,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            set_attempt_admissions(state_path, lock_path, closed=True)
            remaining = prune_stale_active_attempts(state_path, lock_path)

            self.assertEqual([item["attempt_id"] for item in remaining], ["live"])
            self.assertEqual(active_attempts(state_path, lock_path), remaining)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["attempt_admissions_closed"])
            self.assertEqual(state["reserved_tinker_usd"], 3.0)
            self.assertEqual(state["remaining_tinker_cost_usd"], 5.0)


if __name__ == "__main__":
    unittest.main()
