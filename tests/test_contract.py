"""Tests for the workspace contract adapter.

These verify the fail-fast gate and the budget math against synthetic
workspaces, with no dependency on RSIBench-Data or the claude CLI.
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.contract import (
    REQUIRED_WORKSPACE_SCRIPTS,
    ContractError,
    load_contract,
)


def _make_workspace(root: Path) -> dict:
    """Build a minimal valid harness layout and return a contract env dict."""
    session = root / "session"
    work_dir = session / "agent_workspace"
    runner_state = session / "runner_state"
    work_dir.mkdir(parents=True)
    runner_state.mkdir(parents=True)

    for name in REQUIRED_WORKSPACE_SCRIPTS:
        (work_dir / name).write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    prompt = session / "data_agent_prompt.md"
    prompt.write_text("do the thing\n", encoding="utf-8")

    budget = runner_state / "budget_state.json"
    budget.write_text(
        json.dumps(
            {
                "started_at": int(time.time()),
                "budget": {"wall_time_budget_sec": 14400},
                "remaining_tinker_cost_usd": 25.0,
            }
        ),
        encoding="utf-8",
    )

    return {
        "RSIBENCH_DATA_WORK_DIR": str(work_dir),
        "RSIBENCH_DATA_REPO_ROOT": str(root),
        "RSIBENCH_DATA_PROMPT_PATH": str(prompt),
        "ANTHROPIC_MODEL": "claude-test",
    }


class TestLoadContract(unittest.TestCase):
    def test_valid_workspace_loads(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            c = load_contract(env)
            self.assertEqual(c.model, "claude-test")
            self.assertEqual(c.min_remaining_sec, 1800)
            self.assertTrue(c.budget_state_path.is_file())
            self.assertFalse(c.final_selection_exists())

    def test_missing_work_dir_raises(self) -> None:
        with self.assertRaises(ContractError):
            load_contract({})

    def test_missing_script_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            (Path(env["RSIBENCH_DATA_WORK_DIR"]) / "run_attempt.sh").unlink()
            with self.assertRaises(ContractError):
                load_contract(env)

    def test_half_anthropic_config_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            env["ANTHROPIC_BASE_URL"] = "https://example.test"
            # API key intentionally absent.
            with self.assertRaises(ContractError):
                load_contract(env)

    def test_auth_token_with_base_url_loads(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            env["ANTHROPIC_BASE_URL"] = "https://example.test"
            env["ANTHROPIC_AUTH_TOKEN"] = "token"
            self.assertEqual(load_contract(env).model, "claude-test")

    def test_auth_token_without_base_url_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            env["ANTHROPIC_AUTH_TOKEN"] = "token"
            with self.assertRaises(ContractError):
                load_contract(env)

    def test_data_agent_model_overrides_anthropic_model(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            env["DATA_AGENT_MODEL"] = "preferred-model"
            c = load_contract(env)
            self.assertEqual(c.model, "preferred-model")

    def test_codex_model_is_used_for_codex_harness(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            env.pop("ANTHROPIC_MODEL")
            env.update(
                {
                    "DATA_AGENT_HARNESS": "codex",
                    "CODEX_BASE_URL": "https://example.test/v1",
                    "CODEX_API_KEY": "secret",
                    "CODEX_MODEL": "gpt-test",
                }
            )
            self.assertEqual(load_contract(env).model, "gpt-test")

    def test_no_model_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            del env["ANTHROPIC_MODEL"]
            with self.assertRaises(ContractError):
                load_contract(env)


class TestBudgetMath(unittest.TestCase):
    def test_remaining_wall_time_floors_at_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            c = load_contract(env)
            # Rewrite started_at far in the past so the budget is exhausted.
            state = json.loads(c.budget_state_path.read_text(encoding="utf-8"))
            state["started_at"] = int(time.time()) - 100000
            c.budget_state_path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(c.remaining_wall_time(), 0)

    def test_remaining_cost_reads_state(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            c = load_contract(env)
            self.assertEqual(c.remaining_cost(), 25.0)

    def test_unreadable_state_is_treated_as_exhausted(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _make_workspace(Path(tmp))
            c = load_contract(env)
            c.budget_state_path.unlink()
            self.assertEqual(c.remaining_wall_time(), 0)
            self.assertEqual(c.remaining_cost(), 0.0)


if __name__ == "__main__":
    unittest.main()
