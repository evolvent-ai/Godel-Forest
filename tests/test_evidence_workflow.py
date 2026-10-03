"""Tests for the evidence-driven workflow layer."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.evidence_workflow import (
    ASSET_PACKAGE,
    ATTEMPT_REVIEW_FILENAME,
    ATTEMPT_REVIEW_SKILL_PATH,
    CANDIDATE_REFLECTION_SKILL_PATH,
    CANDIDATES_FILENAME,
    CONTINUE_EVAL_SCRIPT_PATH,
    CONTINUE_WORKFLOW_NOTE,
    DATA_PLAN_FILENAME,
    HUMAN_RESEARCH_SKILL_PATH,
    DIAGNOSTIC_EVAL_SKILL_PATH,
    FLOW_FILENAME,
    append_initial_workflow_note,
    install_evidence_workflow,
)
from godel_forest.loop import CONTINUE_PROMPT_TEMPLATE
from importlib.resources import files


class TestEvidenceWorkflowInstall(unittest.TestCase):
    def test_installs_expected_files(self) -> None:
        with TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            result = install_evidence_workflow(work_dir)

            self.assertEqual(
                {path.relative_to(work_dir) for path in result.created},
                {
                    Path(FLOW_FILENAME),
                    Path(DATA_PLAN_FILENAME),
                    Path(ATTEMPT_REVIEW_FILENAME),
                    Path(CANDIDATES_FILENAME),
                    CONTINUE_EVAL_SCRIPT_PATH,
                    HUMAN_RESEARCH_SKILL_PATH,
                    CANDIDATE_REFLECTION_SKILL_PATH,
                    ATTEMPT_REVIEW_SKILL_PATH,
                    DIAGNOSTIC_EVAL_SKILL_PATH,
                },
            )
            self.assertEqual(result.updated, ())
            self.assertEqual(result.preserved, ())
            self.assertEqual(result.removed, ())
            for filename in (
                Path(FLOW_FILENAME),
                Path(DATA_PLAN_FILENAME),
                Path(ATTEMPT_REVIEW_FILENAME),
                Path(CANDIDATES_FILENAME),
                CONTINUE_EVAL_SCRIPT_PATH,
                HUMAN_RESEARCH_SKILL_PATH,
                CANDIDATE_REFLECTION_SKILL_PATH,
                ATTEMPT_REVIEW_SKILL_PATH,
                DIAGNOSTIC_EVAL_SKILL_PATH,
            ):
                expected = files(ASSET_PACKAGE).joinpath(*filename.parts).read_text(
                    encoding="utf-8"
                )
                self.assertEqual(
                    (work_dir / filename).read_text(encoding="utf-8"), expected
                )

    def test_install_is_idempotent_and_preserves_agent_notes(self) -> None:
        with TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            (work_dir / DATA_PLAN_FILENAME).write_text("agent plan\n", encoding="utf-8")
            (work_dir / ATTEMPT_REVIEW_FILENAME).write_text(
                "agent review\n", encoding="utf-8"
            )

            first = install_evidence_workflow(work_dir)
            second = install_evidence_workflow(work_dir)

            self.assertNotIn(work_dir / DATA_PLAN_FILENAME, first.created)
            self.assertIn(work_dir / DATA_PLAN_FILENAME, first.preserved)
            self.assertEqual(
                (work_dir / DATA_PLAN_FILENAME).read_text(encoding="utf-8"),
                "agent plan\n",
            )
            self.assertEqual(second.created, ())
            self.assertEqual(second.updated, ())
            self.assertEqual(
                {path.relative_to(work_dir) for path in second.preserved},
                {
                    Path(FLOW_FILENAME),
                    Path(DATA_PLAN_FILENAME),
                    Path(ATTEMPT_REVIEW_FILENAME),
                    Path(CANDIDATES_FILENAME),
                    CONTINUE_EVAL_SCRIPT_PATH,
                    HUMAN_RESEARCH_SKILL_PATH,
                    CANDIDATE_REFLECTION_SKILL_PATH,
                    ATTEMPT_REVIEW_SKILL_PATH,
                    DIAGNOSTIC_EVAL_SKILL_PATH,
                },
            )

    def test_install_can_place_mutable_state_in_private_worker_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "workspace"
            state_dir = work_dir / "agents" / "agent001"
            legacy_plan = work_dir / DATA_PLAN_FILENAME
            legacy_plan.parent.mkdir(parents=True, exist_ok=True)
            legacy_plan.write_text("legacy shared plan\n", encoding="utf-8")

            result = install_evidence_workflow(work_dir, state_dir=state_dir)

            for filename in (
                DATA_PLAN_FILENAME,
                ATTEMPT_REVIEW_FILENAME,
                CANDIDATES_FILENAME,
            ):
                self.assertTrue((state_dir / filename).is_file())
            self.assertEqual(legacy_plan.read_text(encoding="utf-8"), "legacy shared plan\n")
            self.assertFalse((work_dir / ATTEMPT_REVIEW_FILENAME).exists())
            self.assertFalse((work_dir / CANDIDATES_FILENAME).exists())
            private_plan = (state_dir / DATA_PLAN_FILENAME).read_text(encoding="utf-8")
            self.assertIn(str(work_dir / "attempts"), private_plan)
            self.assertIn(str(work_dir / "run_attempt.sh"), private_plan)
            self.assertIn(str(work_dir / "continue_eval.sh"), private_plan)
            self.assertTrue((work_dir / FLOW_FILENAME).is_file())
            self.assertTrue((work_dir / CONTINUE_EVAL_SCRIPT_PATH).is_file())
            self.assertIn(state_dir / DATA_PLAN_FILENAME, result.created)

    def test_refreshes_managed_assets_and_removes_legacy_skill(self) -> None:
        with TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            flow = work_dir / FLOW_FILENAME
            old_skills = (
                work_dir / ".claude/skills/deepresearch/SKILL.md",
                work_dir / ".claude/skills/deep-research/SKILL.md",
            )
            plan = work_dir / DATA_PLAN_FILENAME
            flow.write_text("old flow\n", encoding="utf-8")
            for old_skill in old_skills:
                old_skill.parent.mkdir(parents=True)
                old_skill.write_text("old skill\n", encoding="utf-8")
            plan.write_text("agent-authored plan\n", encoding="utf-8")

            result = install_evidence_workflow(work_dir)

            self.assertIn(flow, result.updated)
            for old_skill in old_skills:
                self.assertIn(old_skill, result.removed)
                self.assertFalse(old_skill.exists())
            self.assertEqual(plan.read_text(encoding="utf-8"), "agent-authored plan\n")
            expected_flow = files(ASSET_PACKAGE).joinpath(FLOW_FILENAME).read_text(
                encoding="utf-8"
            )
            self.assertEqual(flow.read_text(encoding="utf-8"), expected_flow)


class TestEvidenceWorkflowPromptNotes(unittest.TestCase):
    def test_assets_define_generic_eval_id_boundary(self) -> None:
        flow = files(ASSET_PACKAGE).joinpath(FLOW_FILENAME).read_text(encoding="utf-8")
        diagnostic = files(ASSET_PACKAGE).joinpath(
            *DIAGNOSTIC_EVAL_SKILL_PATH.parts
        ).read_text(encoding="utf-8")
        continue_script = files(ASSET_PACKAGE).joinpath(
            *CONTINUE_EVAL_SCRIPT_PATH.parts
        ).read_text(encoding="utf-8")
        candidate = files(ASSET_PACKAGE).joinpath(
            *CANDIDATE_REFLECTION_SKILL_PATH.parts
        ).read_text(encoding="utf-8")
        research = files(ASSET_PACKAGE).joinpath(
            *HUMAN_RESEARCH_SKILL_PATH.parts
        ).read_text(encoding="utf-8")

        for content in (flow, candidate, research):
            normalized = " ".join(content.split())
            self.assertIn("canonical task or item IDs", normalized)
            self.assertIn("intersection", normalized)
            self.assertIn(
                "do not introduce additional contamination criteria", normalized
            )
        self.assertIn("Prefer authentic source material", flow)
        self.assertIn("continue_eval.sh <same_attempt_id>", diagnostic)
        self.assertIn("godel_forest.continue_eval", continue_script)
        self.assertNotIn("oracle-shaped", flow)
        self.assertNotIn("oracle-known", candidate)

    def test_initial_prompt_note_preserves_original_prompt_prefix(self) -> None:
        prompt = append_initial_workflow_note("original RSIBench-Data prompt")
        self.assertTrue(prompt.startswith("original RSIBench-Data prompt"))
        self.assertIn(FLOW_FILENAME, prompt)
        self.assertIn("does not replace", prompt)
        self.assertIn("/human-research", prompt)
        self.assertIn("/candidate-reflection", prompt)
        self.assertIn("/attempt-review", prompt)
        self.assertIn("execution-verified", prompt)

    def test_initial_prompt_note_can_use_absolute_workspace_paths(self) -> None:
        prompt = append_initial_workflow_note(
            "original RSIBench-Data prompt", work_dir=Path("/workspace")
        )
        self.assertIn("/workspace/GODEL_FOREST_FLOW.md", prompt)
        self.assertIn("/workspace/DATA_PLAN.md", prompt)
        self.assertIn("/workspace/ATTEMPT_REVIEW.md", prompt)
        self.assertIn("/workspace/CANDIDATES.jsonl", prompt)
        self.assertIn("bash /workspace/continue_eval.sh", prompt)
        self.assertNotIn("`GODEL_FOREST_FLOW.md`", prompt)

    def test_continue_prompt_includes_workflow_note(self) -> None:
        prompt = CONTINUE_PROMPT_TEMPLATE.format(status="ok")
        self.assertIn("Current budget status", prompt)
        self.assertIn("ok", prompt)
        self.assertIn(CONTINUE_WORKFLOW_NOTE, prompt)
        self.assertIn(DATA_PLAN_FILENAME, prompt)
        self.assertIn(ATTEMPT_REVIEW_FILENAME, prompt)
        self.assertIn(CANDIDATES_FILENAME, prompt)


if __name__ == "__main__":
    unittest.main()
