"""Tests for the re-prompt loop and claude driver flag assembly.

The claude CLI is never actually launched here; we stub the driver to record
how many times print/continue are called and to simulate final-selection
appearance and budget exhaustion.
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.contract import REQUIRED_WORKSPACE_SCRIPTS, load_contract
from godel_forest.loop import run_loop


def _workspace(root: Path, *, cost: float = 25.0, wall: int = 14400) -> dict:
    session = root / "session"
    work_dir = session / "agent_workspace"
    runner_state = session / "runner_state"
    work_dir.mkdir(parents=True)
    runner_state.mkdir(parents=True)
    for name in REQUIRED_WORKSPACE_SCRIPTS:
        # budget_status.sh must be runnable: make it a trivial script.
        (work_dir / name).write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    prompt = session / "data_agent_prompt.md"
    prompt.write_text("go\n", encoding="utf-8")
    (runner_state / "budget_state.json").write_text(
        json.dumps(
            {
                "started_at": int(time.time()),
                "budget": {"wall_time_budget_sec": wall},
                "remaining_tinker_cost_usd": cost,
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


class _FakeDriver:
    """Stub driver that records calls and can write final_selection.json."""

    def __init__(self, contract, *, submit_after: int | None = None) -> None:
        self.contract = contract
        self.env = {}
        self.print_calls = 0
        self.continue_calls = 0
        self.submit_after = submit_after
        self.last_print_prompt = ""
        self.last_continue_prompt = ""

    def _maybe_submit(self) -> None:
        total = self.print_calls + self.continue_calls
        if self.submit_after is not None and total >= self.submit_after:
            self.contract.final_selection_path.write_text("{}", encoding="utf-8")

    def run_print(self, prompt: str, log_path: Path) -> int:
        self.print_calls += 1
        self.last_print_prompt = prompt
        self._maybe_submit()
        return 0

    def run_continue(self, prompt: str, log_path: Path) -> int:
        self.continue_calls += 1
        self.last_continue_prompt = prompt
        self._maybe_submit()
        return 0


class TestRunLoop(unittest.TestCase):
    def test_submits_on_initial_print_no_continue(self) -> None:
        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp)))
            driver = _FakeDriver(c, submit_after=1)
            run_loop(c, driver)
            self.assertEqual(driver.print_calls, 1)
            self.assertEqual(driver.continue_calls, 0)
            self.assertTrue(c.final_selection_exists())

    def test_continues_until_final_selection(self) -> None:
        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp)))
            driver = _FakeDriver(c, submit_after=3)
            run_loop(c, driver)
            self.assertEqual(driver.print_calls, 1)
            self.assertEqual(driver.continue_calls, 2)

    def test_stops_when_cost_exhausted(self) -> None:
        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp), cost=0.0))
            driver = _FakeDriver(c, submit_after=None)
            run_loop(c, driver)
            # Initial print runs, then loop sees cost<=0 and stops immediately.
            self.assertEqual(driver.print_calls, 1)
            self.assertEqual(driver.continue_calls, 0)

    def test_stops_when_wall_time_below_min(self) -> None:
        with TemporaryDirectory() as tmp:
            env = _workspace(Path(tmp), wall=1000)  # below default MIN 1800
            c = load_contract(env)
            driver = _FakeDriver(c, submit_after=None)
            run_loop(c, driver)
            self.assertEqual(driver.print_calls, 1)
            self.assertEqual(driver.continue_calls, 0)

    def test_initial_prompt_preserves_harness_prompt_and_adds_workflow_note(self) -> None:
        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp), cost=0.0))
            driver = _FakeDriver(c, submit_after=None)
            run_loop(c, driver)
            self.assertTrue(driver.last_print_prompt.startswith("go"))
            self.assertIn("GODEL_FOREST_FLOW.md", driver.last_print_prompt)
            self.assertIn("does not replace", driver.last_print_prompt)


class TestDriverFlags(unittest.TestCase):
    def test_codex_exec_and_resume_commands(self) -> None:
        from godel_forest.claude_driver import CodexDriver

        with TemporaryDirectory() as tmp:
            env = _workspace(Path(tmp))
            env.update(
                {
                    "DATA_AGENT_HARNESS": "codex",
                    "CODEX_BASE_URL": "https://example.test/v1",
                    "CODEX_API_KEY": "secret",
                    "CODEX_MODEL": "gpt-test",
                    "CODEX_MODEL_REASONING_EFFORT": "high",
                }
            )
            driver = CodexDriver(contract=load_contract(env), env=env)
            driver._proxy_url = "http://127.0.0.1:12345/v1"

            initial = driver._command("start", Path(tmp) / "initial.last.txt", resume=False)
            resumed = driver._command("continue", Path(tmp) / "continue.last.txt", resume=True)

            self.assertEqual(
                initial[0:7],
                [
                    "codex",
                    "--ask-for-approval",
                    "never",
                    "--cd",
                    str(driver.contract.work_dir),
                    "exec",
                    "--ignore-user-config",
                ],
            )
            self.assertIn("--json", initial)
            self.assertIn("--output-last-message", initial)
            self.assertIn("model_reasoning_effort=\"high\"", initial)
            self.assertIn("continue", resumed)
            self.assertIn("resume", resumed)
            self.assertIn("--last", resumed)

    def test_bare_mode_when_custom_endpoint(self) -> None:
        from godel_forest.claude_driver import ClaudeDriver

        with TemporaryDirectory() as tmp:
            env = _workspace(Path(tmp))
            c = load_contract(env)
            driver_env = {
                "ANTHROPIC_BASE_URL": "https://x.test",
                "ANTHROPIC_API_KEY": "secret",
            }
            driver = ClaudeDriver(contract=c, env=driver_env)
            self.assertIn("--bare", driver._auth_args)
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", driver.env)
            self.assertEqual(driver.env["ANTHROPIC_MODEL"], "claude-test")

    def test_no_bare_mode_without_custom_endpoint(self) -> None:
        from godel_forest.claude_driver import ClaudeDriver

        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp)))
            driver = ClaudeDriver(contract=c, env={})
            self.assertEqual(driver._auth_args, [])

    def test_auth_token_kept_without_bare_mode(self) -> None:
        from godel_forest.claude_driver import ClaudeDriver

        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp)))
            driver = ClaudeDriver(
                contract=c,
                env={
                    "ANTHROPIC_BASE_URL": "https://x.test",
                    "ANTHROPIC_AUTH_TOKEN": "token",
                    "ANTHROPIC_API_KEY": "lower-priority-key",
                },
            )
            self.assertEqual(driver._auth_args, [])
            self.assertEqual(driver.env["ANTHROPIC_AUTH_TOKEN"], "token")
            self.assertNotIn("ANTHROPIC_API_KEY", driver.env)

    def test_common_flags_include_stream_json(self) -> None:
        from godel_forest.claude_driver import ClaudeDriver

        with TemporaryDirectory() as tmp:
            c = load_contract(_workspace(Path(tmp)))
            driver = ClaudeDriver(contract=c, env={})
            flags = driver._common_flags()
            self.assertIn("stream-json", flags)
            self.assertIn("--allowedTools", flags)
            self.assertIn(c.model, flags)


if __name__ == "__main__":
    unittest.main()
