"""Tests for multi-agent leaderboard rendering."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.multiagent.leaderboard import best_completed_attempt, completed_attempts, render_leaderboard


class TestMultiAgentLeaderboard(unittest.TestCase):
    def test_best_uses_highest_completed_numeric_score(self) -> None:
        state = {
            "iterations": [
                {"attempt_id": "a", "status": "completed", "harbor_score": 0.1},
                {"attempt_id": "b", "status": "failed", "harbor_score": 0.9},
                {"attempt_id": "c", "status": "completed", "harbor_score": 0.4},
                {"attempt_id": "d", "status": "completed", "harbor_score": None},
            ]
        }
        best = best_completed_attempt(state)
        self.assertIsNotNone(best)
        self.assertEqual(best.attempt_id, "c")
        self.assertEqual(best.harbor_score, 0.4)

    def test_best_ignores_subset_scores(self) -> None:
        state = {
            "iterations": [
                {"attempt_id": "full", "status": "completed", "harbor_score": 0.4},
                {
                    "attempt_id": "subset",
                    "status": "completed",
                    "harbor_score": 1.0,
                    "evaluation_scope": "subset",
                },
            ]
        }

        best = best_completed_attempt(state)

        self.assertIsNotNone(best)
        self.assertEqual(best.attempt_id, "full")

    def test_completed_attempts_only_include_full_set_results(self) -> None:
        state = {
            "iterations": [
                {"attempt_id": "legacy-full", "status": "completed", "harbor_score": 0.4},
                {
                    "attempt_id": "full",
                    "status": "completed",
                    "harbor_score": 0.3,
                    "evaluation_scope": "full",
                },
                {
                    "attempt_id": "subset",
                    "status": "completed",
                    "harbor_score": 1.0,
                    "evaluation_scope": "subset",
                },
                {
                    "attempt_id": "continuation",
                    "status": "completed",
                    "harbor_score": 0.9,
                    "evaluation_scope": "continuation",
                },
            ]
        }

        attempts = completed_attempts(state)

        self.assertEqual([attempt.attempt_id for attempt in attempts], ["legacy-full", "full"])

    def test_render_leaderboard_writes_public_json_under_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = root / "budget_state.json"
            out = root / "public" / "leaderboard.json"
            lock = root / "locks" / "budget.lock"
            budget.write_text(
                json.dumps(
                    {
                        "run_id": "r",
                        "remaining_tinker_cost_usd": 3.0,
                        "iterations": [
                            {"attempt_id": "x", "status": "completed", "harbor_score": 0.2},
                            {
                                "attempt_id": "subset",
                                "status": "completed",
                                "harbor_score": 0.9,
                                "evaluation_scope": "subset",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = render_leaderboard(budget, out, lock)
            self.assertEqual(payload["best"]["attempt_id"], "x")
            self.assertEqual([attempt["attempt_id"] for attempt in payload["attempts"]], ["x"])
            self.assertEqual(payload["attempts"][0]["evaluation_scope"], "full")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["run_id"], "r")

    def test_render_leaderboard_includes_precomputed_full_set_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = root / "runner_state" / "budget_state.json"
            out = root / ".godel_forest" / "public" / "leaderboard.json"
            benchmark = root / "benchmarks" / "example"
            result = benchmark / "runs" / "base" / "harbor" / "jobs" / "base" / "result.json"
            budget.parent.mkdir(parents=True)
            result.parent.mkdir(parents=True)
            (benchmark / "spec.json").write_text(
                json.dumps(
                    {
                        "target_benchmark": "example-benchmark",
                        "official_eval": {"n_tasks": 2},
                    }
                ),
                encoding="utf-8",
            )
            result.write_text(
                json.dumps(
                    {
                        "stats": {
                            "evals": {
                                "agent__Base/Model__full": {
                                    "n_trials": 2,
                                    "metrics": [{"mean": 0.5}],
                                    "reward_stats": {
                                        "reward": {
                                            "1.0": ["task-a"],
                                            "0.0": ["task-b"],
                                        }
                                    },
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            budget.write_text(
                json.dumps(
                    {
                        "run_id": "r",
                        "target_model": "Base/Model",
                        "target_benchmark": "example-benchmark",
                        "iterations": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = render_leaderboard(budget, out)

            self.assertEqual(payload["baseline"]["model"], "Base/Model")
            self.assertEqual(payload["baseline"]["harbor_score"], 0.5)
            self.assertEqual(payload["baseline"]["evaluation_scope"], "full")
            self.assertEqual(payload["baseline"]["solved_task_ids"], ["task-a"])
            self.assertEqual(payload["baseline"]["unsolved_task_ids"], ["task-b"])
            self.assertEqual(
                payload["baseline"]["harbor_result_path"],
                "benchmarks/example/runs/base/harbor/jobs/base/result.json",
            )
            self.assertEqual(payload["attempts"], [])
            self.assertIsNone(payload["best"])

    def test_render_leaderboard_rejects_incomplete_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = root / "budget_state.json"
            out = root / ".godel_forest" / "public" / "leaderboard.json"
            benchmark = root / "benchmarks" / "example"
            result = benchmark / "runs" / "base" / "harbor" / "jobs" / "base" / "result.json"
            result.parent.mkdir(parents=True)
            (benchmark / "spec.json").write_text(
                json.dumps(
                    {
                        "target_benchmark": "example-benchmark",
                        "official_eval": {"n_tasks": 100},
                    }
                ),
                encoding="utf-8",
            )
            result.write_text(
                json.dumps(
                    {
                        "stats": {
                            "evals": {
                                "agent__Base/Model__subset": {
                                    "n_trials": 10,
                                    "metrics": [{"mean": 0.9}],
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            budget.write_text(
                json.dumps(
                    {
                        "target_model": "Base/Model",
                        "target_benchmark": "example-benchmark",
                        "iterations": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = render_leaderboard(budget, out)

            self.assertIsNone(payload["baseline"])


if __name__ == "__main__":
    unittest.main()
