"""Tests for manager-only best-attempt finalization."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.contract import REQUIRED_WORKSPACE_SCRIPTS, load_contract
from godel_forest.multiagent.finalizer import BestAttemptFinalizer


def _workspace(root: Path, iterations: list[dict]) -> object:
    session = root / "session"
    work = session / "agent_workspace"
    state_dir = session / "runner_state"
    work.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    for name in REQUIRED_WORKSPACE_SCRIPTS:
        (work / name).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    prompt = session / "prompt.md"
    prompt.write_text("go\n", encoding="utf-8")
    (state_dir / "budget_state.json").write_text(
        json.dumps(
            {
                "started_at": 0,
                "budget": {"wall_time_budget_sec": 10000},
                "remaining_tinker_cost_usd": 10.0,
                "iterations": iterations,
            }
        ),
        encoding="utf-8",
    )
    return load_contract(
        {
            "RSIBENCH_DATA_WORK_DIR": str(work),
            "RSIBENCH_DATA_REPO_ROOT": str(root),
            "RSIBENCH_DATA_PROMPT_PATH": str(prompt),
            "ANTHROPIC_MODEL": "m",
        }
    )


class TestBestAttemptFinalizer(unittest.TestCase):
    def test_submits_best_completed_once(self) -> None:
        with TemporaryDirectory() as tmp:
            contract = _workspace(
                Path(tmp),
                [
                    {"attempt_id": "a", "status": "completed", "harbor_score": 0.1},
                    {"attempt_id": "b", "status": "completed", "harbor_score": 0.3},
                    {
                        "attempt_id": "subset",
                        "status": "completed",
                        "harbor_score": 0.9,
                        "evaluation_scope": "subset",
                    },
                ],
            )
            calls: list[list[str]] = []

            def runner(cmd, **kwargs):
                calls.append(cmd)
                class Result:
                    returncode = 0
                return Result()

            f = BestAttemptFinalizer(contract, contract.work_dir / ".godel_forest/private/locks/budget.lock", runner)
            result = f.submit_best(env={})
            self.assertTrue(result.submitted)
            self.assertEqual(result.attempt_id, "b")
            self.assertEqual(calls[0][-1], "b")

    def test_no_completed_attempt_does_not_submit(self) -> None:
        with TemporaryDirectory() as tmp:
            contract = _workspace(Path(tmp), [{"attempt_id": "a", "status": "failed", "harbor_score": 1.0}])
            calls: list[list[str]] = []
            f = BestAttemptFinalizer(
                contract,
                contract.work_dir / ".godel_forest/private/locks/budget.lock",
                lambda cmd, **kwargs: calls.append(cmd),
            )
            result = f.submit_best(env={})
            self.assertFalse(result.submitted)
            self.assertEqual(result.reason, "no_completed_attempt")
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
