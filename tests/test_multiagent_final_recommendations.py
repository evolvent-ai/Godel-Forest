"""Tests for per-agent terminal recommendation validation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.multiagent.final_recommendations import (
    TerminalRecommendation,
    read_terminal_recommendations,
    write_terminal_recommendation,
)


class TestTerminalRecommendations(unittest.TestCase):
    def test_accepts_completed_full_agent_recommendation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            budget = root / "budget_state.json"
            lock = root / "budget.lock"
            budget.write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "attempt_id": "agent001_001",
                                "status": "completed",
                                "harbor_score": 0.4,
                                "evaluation_scope": "full",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            write_terminal_recommendation(
                public,
                TerminalRecommendation(
                    agent_id="agent001",
                    decision="submit",
                    source="agent",
                    reason="No better candidate is justified.",
                    reason_type="agent_decision",
                    attempt_id="agent001_001",
                    score=0.4,
                    evaluation_scope="full",
                ),
            )

            recommendations = read_terminal_recommendations(
                public, ("agent001",), budget, lock
            )

            self.assertEqual(recommendations["agent001"].attempt_id, "agent001_001")

    def test_rejects_subset_recommendation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            budget = root / "budget_state.json"
            lock = root / "budget.lock"
            budget.write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "attempt_id": "agent001_001",
                                "status": "completed",
                                "harbor_score": 1.0,
                                "evaluation_scope": "subset",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            write_terminal_recommendation(
                public,
                TerminalRecommendation(
                    agent_id="agent001",
                    decision="submit",
                    source="agent",
                    reason="Subset looks promising.",
                    reason_type="agent_decision",
                    attempt_id="agent001_001",
                    score=1.0,
                    evaluation_scope="subset",
                ),
            )

            recommendations = read_terminal_recommendations(
                public, ("agent001",), budget, lock
            )

            self.assertEqual(recommendations, {})

    def test_accepts_manager_error_timeout_without_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            budget = root / "budget_state.json"
            lock = root / "budget.lock"
            budget.write_text("{\"iterations\": []}", encoding="utf-8")
            write_terminal_recommendation(
                public,
                TerminalRecommendation(
                    agent_id="agent001",
                    decision="submit",
                    source="manager",
                    reason="error timeout",
                    reason_type="agent_error_timeout",
                ),
            )

            recommendations = read_terminal_recommendations(
                public, ("agent001",), budget, lock
            )

            self.assertEqual(recommendations["agent001"].source, "manager")


if __name__ == "__main__":
    unittest.main()
