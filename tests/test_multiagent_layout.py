"""Tests for parallel worker workspace creation."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.multiagent.config import MultiAgentConfig
from godel_forest.multiagent.layout import SHARED_LINKS, create_multiagent_layout
from godel_forest.multiagent.prompts import render_initial_prompt, render_resume_prompt


class TestMultiAgentLayout(unittest.TestCase):
    def test_creates_public_private_and_agent_workspaces(self) -> None:
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            cfg = MultiAgentConfig(enabled=True, agent_ids=("agent001", "agent002"), model="m")
            paths = create_multiagent_layout(work, cfg)

            self.assertTrue((work / ".godel_forest" / "public").is_dir())
            self.assertTrue((work / ".godel_forest" / "private" / "locks").is_dir())
            for skill in (
                "human-research",
                "candidate-reflection",
                "diagnostic-eval-plan",
                "attempt-review",
            ):
                self.assertTrue((paths.public / "skills" / skill / "SKILL.md").is_file())
            self.assertFalse((paths.public / "skills" / "quick-signal-eval").exists())
            diagnostic_skill = (
                paths.public / "skills" / "diagnostic-eval-plan" / "SKILL.md"
            ).read_text(
                encoding="utf-8"
            )
            self.assertIn("eval_task_ids.json", diagnostic_skill)
            self.assertIn("--task-ids-file", diagnostic_skill)
            self.assertIn("continue_eval.sh <same_attempt_id>", diagnostic_skill)
            self.assertFalse((paths.public / "roles").exists())

            for agent_id in cfg.agent_ids:
                workspace = work / "agents" / agent_id
                agent_doc = workspace / "GODEL_FOREST_AGENT.md"
                self.assertTrue(agent_doc.is_file())
                doc = agent_doc.read_text(encoding="utf-8")
                self.assertIn("independent replica", doc)
                self.assertIn("human-research", doc)
                self.assertIn("candidate-reflection", doc)
                self.assertIn("neutral worker id", doc)
                self.assertIn("eval_task_ids.json", doc)
                self.assertIn("--task-ids-file", doc)
                self.assertIn("continue_eval.sh", doc)
                self.assertIn("Never use `nohup`", doc)
                self.assertIn(str(work.resolve() / "GODEL_FOREST_FLOW.md"), doc)
                self.assertIn(str(workspace.resolve() / ".claude"), doc)
                for filename in (
                    "DATA_PLAN.md",
                    "ATTEMPT_REVIEW.md",
                    "CANDIDATES.jsonl",
                ):
                    self.assertTrue((workspace / filename).is_file())
                    self.assertIn(str((workspace / filename).resolve()), doc)
                    self.assertFalse((work / filename).exists())
                self.assertNotIn("../../run_attempt.sh", doc)
                self.assertNotIn("explorer", doc)
                self.assertNotIn("builder", doc)
                self.assertNotIn("critic", doc)
                for name in SHARED_LINKS:
                    link = workspace / ".claude" / name
                    self.assertTrue(link.is_symlink(), link)
                    resolved = (link.parent / os.readlink(link)).resolve()
                    self.assertEqual(resolved, (paths.public / name).resolve())

    def test_settings_guard_final_submit_and_private(self) -> None:
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            cfg = MultiAgentConfig(enabled=True, agent_ids=("agent001",), model="m")
            paths = create_multiagent_layout(work, cfg)
            settings = json.loads(
                (work / "agents" / "agent001" / ".claude" / "settings.local.json").read_text(
                    encoding="utf-8"
                )
            )
            deny = settings["permissions"]["deny"]
            allow = settings["permissions"]["allow"]
            self.assertIn("Bash(*final_submit.sh*)", deny)
            self.assertIn("Bash(*nohup*)", deny)
            self.assertIn("Bash(*disown*)", deny)
            self.assertIn("Bash(*setsid*)", deny)
            self.assertTrue(any(str(paths.private.resolve()) in rule and rule.startswith("Bash") for rule in deny))
            self.assertTrue(any(str(paths.private.resolve()) in rule for rule in deny))
            self.assertTrue(any(str(paths.public.resolve()) in rule for rule in allow))
            self.assertTrue(any("attempts/agent001_*" in rule for rule in allow))
            self.assertTrue(
                any(
                    str((work / "agents" / "agent001").resolve()) in rule
                    for rule in allow
                )
            )
            self.assertFalse(
                any(str((work / "DATA_PLAN.md").resolve()) in rule for rule in allow)
            )
            self.assertFalse(
                any(
                    str((work / "ATTEMPT_REVIEW.md").resolve()) in rule
                    for rule in allow
                )
            )

    def test_layout_is_idempotent_and_rewrites_worker_doc(self) -> None:
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            cfg = MultiAgentConfig(enabled=True, agent_ids=("agent001",), model="m")
            create_multiagent_layout(work, cfg)
            doc = work / "agents" / "agent001" / "GODEL_FOREST_AGENT.md"
            doc.write_text("stale doc\n", encoding="utf-8")
            create_multiagent_layout(work, cfg)
            self.assertIn("independent replica", doc.read_text(encoding="utf-8"))

    def test_layout_removes_obsolete_gate_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            cfg = MultiAgentConfig(enabled=True, agent_ids=("agent001",), model="m")
            paths = create_multiagent_layout(work, cfg)
            stale_skill = paths.public / "skills" / "quick-signal-eval"
            stale_logs = paths.public / "logs" / "gated_attempts"
            stale_bin = paths.public / "bin"
            stale_skill.mkdir(parents=True)
            stale_logs.mkdir(parents=True)
            stale_bin.mkdir(parents=True)
            (stale_skill / "SKILL.md").write_text("stale", encoding="utf-8")
            (stale_logs / "old.log").write_text("stale", encoding="utf-8")
            (stale_bin / "gated_attempt.sh").write_text("stale", encoding="utf-8")

            create_multiagent_layout(work, cfg)

            self.assertFalse(stale_skill.exists())
            self.assertFalse(stale_logs.exists())
            self.assertFalse(stale_bin.exists())

    def test_worker_prompts_explain_standard_subset_interface(self) -> None:
        work = Path("/workspace")
        worker = work / "agents" / "agent001"
        initial = render_initial_prompt(
            "agent001", work, "base prompt", worker_workspace=worker
        )
        resume = render_resume_prompt(
            "agent001", work, worker_workspace=worker
        )

        self.assertIn("eval_task_ids.json", initial)
        self.assertIn("--task-ids-file", initial)
        self.assertIn("bash /workspace/continue_eval.sh <attempt_id>", initial)
        self.assertIn("/workspace/GODEL_FOREST_FLOW.md", initial)
        self.assertIn("/workspace/agents/agent001/.claude", initial)
        self.assertIn("/workspace/agents/agent001/DATA_PLAN.md", initial)
        self.assertNotIn("/workspace/DATA_PLAN.md", initial)
        self.assertIn("/workspace/final_selection.json", initial)
        self.assertNotIn("../../run_attempt.sh", initial)
        self.assertIn(
            "Never launch `/workspace/run_attempt.sh` or `/workspace/continue_eval.sh`",
            initial,
        )
        self.assertIn("one active attempt per worker", initial)
        self.assertIn("final_recommendations/agent001.json", initial)
        self.assertIn("owner reservations", resume)
        self.assertIn("`/workspace/run_attempt.sh`", resume)
        self.assertIn("--task-ids-file", resume)
        self.assertIn("bash /workspace/continue_eval.sh", resume)
        self.assertNotIn("../../run_attempt.sh", resume)


if __name__ == "__main__":
    unittest.main()
