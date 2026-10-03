"""Tests for checkpoint-preserving continuation evaluation."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from godel_forest.continue_eval import ContinuationError, continue_evaluation, task_outcomes


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def harbor_result(rewards: dict[str, float], *, errors: tuple[str, ...] = ()) -> dict:
    reward_map: dict[str, list[str]] = {}
    for task_id, reward in rewards.items():
        reward_map.setdefault(str(float(reward)), []).append(f"{task_id}__trial")
    return {
        "stats": {
            "evals": {
                "eval": {
                    "n_trials": len(rewards) + len(errors),
                    "n_errors": len(errors),
                    "metrics": [{"mean": sum(rewards.values()) / max(1, len(rewards))}],
                    "reward_stats": {"reward": reward_map},
                    "exception_stats": {
                        "RuntimeError": [f"{task_id}__trial" for task_id in errors]
                    },
                }
            }
        }
    }


class TestContinueEval(unittest.TestCase):
    def test_reuses_checkpoint_and_promotes_same_attempt_to_full(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "RSIBench-Data"
            session_id = "session"
            attempt_id = "agent1_001"
            attempt_run_id = f"{session_id}-{attempt_id}"
            workspace = repo_root / "artifacts" / "runs" / session_id / "agent_workspace"
            attempt_dir = workspace / "attempts" / attempt_id
            source_run = repo_root / "artifacts" / "runs" / attempt_run_id
            budget_state = workspace.parent / "runner_state" / "budget_state.json"
            dataset = repo_root / "benchmarks" / "test" / "datasets" / "full_4"
            for task_id in ("task-1", "task-2", "task-3", "task-4"):
                (dataset / task_id).mkdir(parents=True)
                (dataset / task_id / "task.toml").write_text("", encoding="utf-8")
            attempt_dir.mkdir(parents=True)

            model_path = "tinker://training/sampler_weights/checkpoint"
            write_json(
                source_run / "tinker_train_metadata.json",
                {
                    "model_path": model_path,
                    "processed_train_tokens": 1000,
                    "estimated_train_usd": 5.0,
                },
            )
            source_cost_path = source_run / "tinker_cost_estimate.json"
            write_json(
                source_cost_path,
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "estimated_total_usd": 1.0,
                    "harbor_dataset": "benchmarks/test/datasets/full_4",
                    "harbor_agent": "mini-swe-agent",
                    "agent_config_profile": "mini_swe_agent",
                    "n_concurrent": 4,
                    "n_attempts": 1,
                    "step_limit": 200,
                    "task_ids": ["task-1", "task-2"],
                },
            )
            source_result_path = source_run / "harbor" / "jobs" / "source" / "result.json"
            write_json(source_result_path, harbor_result({"task-1": 1.0, "task-2": 0.0}))
            write_json(
                budget_state,
                {
                    "run_id": session_id,
                    "started_at": 1,
                    "target_model": "Qwen/Qwen3.5-35B-A3B-Base",
                    "budget": {
                        "wall_time_budget_sec": 9999999999,
                        "tinker_cost_budget_usd": 100.0,
                    },
                    "active_attempts": [],
                    "attempt_admissions_closed": False,
                    "iterations": [
                        {
                            "attempt_id": attempt_id,
                            "attempt_run_id": attempt_run_id,
                            "status": "completed",
                            "return_code": 0,
                            "evaluation_scope": "subset",
                            "evaluation_task_ids": ["task-1", "task-2"],
                            "evaluation_n_tasks": 2,
                            "harbor_score": 0.5,
                            "harbor_errors": 0,
                            "harbor_result_path": str(source_result_path),
                            "estimated_tinker_usd": 6.0,
                            "tinker_cost_detail": {
                                "estimated_train_usd": 5.0,
                                "estimated_sample_usd": 1.0,
                                "estimated_total_usd": 6.0,
                                "sample_prompt_tokens": 100,
                                "sample_completion_tokens": 20,
                                "sample_total_tokens": 120,
                                "sample_cost_path": str(source_cost_path),
                            },
                        }
                    ],
                },
            )

            observed_command: list[str] = []

            def fake_runner(command, *, cwd, check):
                observed_command.extend(command)
                self.assertEqual(Path(cwd), repo_root)
                self.assertEqual(command[command.index("--model-path") + 1], model_path)
                self.assertEqual(command[command.index("--infra-retries") + 1], "1")
                run_dir = Path(command[command.index("--run-dir") + 1])
                task_ids_path = Path(command[command.index("--task-ids-file") + 1])
                self.assertEqual(json.loads(task_ids_path.read_text()), ["task-3", "task-4"])
                write_json(
                    run_dir / "harbor" / "jobs" / "continued" / "result.json",
                    harbor_result({"task-3": 1.0, "task-4": 0.0}),
                )
                write_json(
                    run_dir / "tinker_cost_estimate.json",
                    {
                        "prompt_tokens": 200,
                        "completion_tokens": 40,
                        "estimated_total_usd": 2.0,
                        "task_ids": ["task-3", "task-4"],
                    },
                )
                return subprocess.CompletedProcess(command, 0)

            with patch.dict(os.environ, {"DGB_INFRA_RETRIES": "1"}):
                return_code = continue_evaluation(
                    workspace,
                    repo_root,
                    attempt_id,
                    owner_id="agent1",
                    runner=fake_runner,
                )

            self.assertEqual(return_code, 0)
            self.assertIn("backend.e2b_eval", observed_command)
            self.assertNotIn("train_lora_sft", " ".join(observed_command))
            state = json.loads(budget_state.read_text(encoding="utf-8"))
            iteration = state["iterations"][0]
            self.assertEqual(iteration["attempt_id"], attempt_id)
            self.assertEqual(iteration["attempt_run_id"], attempt_run_id)
            self.assertEqual(iteration["evaluation_scope"], "full")
            self.assertEqual(iteration["evaluation_n_tasks"], 4)
            self.assertEqual(iteration["harbor_score"], 0.5)
            self.assertEqual(iteration["estimated_tinker_usd"], 8.0)
            self.assertEqual(iteration["harbor_result_path"], str(source_result_path))
            self.assertEqual(iteration["tinker_cost_detail"]["estimated_train_usd"], 5.0)
            self.assertEqual(iteration["tinker_cost_detail"]["estimated_sample_usd"], 3.0)
            self.assertEqual(
                iteration["tinker_cost_detail"],
                {
                    "train_tokens": 1000,
                    "estimated_train_usd": 5.0,
                    "sample_prompt_tokens": 300,
                    "sample_completion_tokens": 60,
                    "sample_total_tokens": 360,
                    "estimated_sample_usd": 3.0,
                    "estimated_total_usd": 8.0,
                    "train_metadata_path": str(source_run / "tinker_train_metadata.json"),
                    "sample_cost_path": str(source_cost_path),
                    "task_ids": ["task-1", "task-2", "task-3", "task-4"],
                },
            )
            self.assertEqual(state["active_attempts"], [])
            self.assertEqual(state["best_attempt"]["attempt_id"], attempt_id)
            initial_result = json.loads(
                (source_result_path.parent / "initial_subset_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(initial_result, harbor_result({"task-1": 1.0, "task-2": 0.0}))
            canonical_result = json.loads(source_result_path.read_text(encoding="utf-8"))
            self.assertEqual(canonical_result["n_total_trials"], 4)
            self.assertEqual(canonical_result["stats"]["evals"]["aggregate"]["metrics"], [{"mean": 0.5}])
            canonical_cost = json.loads(source_cost_path.read_text(encoding="utf-8"))
            self.assertEqual(canonical_cost["prompt_tokens"], 300)
            self.assertEqual(canonical_cost["completion_tokens"], 60)
            self.assertEqual(canonical_cost["estimated_total_usd"], 3.0)
            self.assertEqual(canonical_cost["task_ids"], ["task-1", "task-2", "task-3", "task-4"])
            initial_cost = json.loads(
                (source_run / "initial_subset_tinker_cost_estimate.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(initial_cost["estimated_total_usd"], 1.0)
            summary = json.loads(
                (attempt_dir / "continued_eval_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["model_path"], model_path)
            self.assertEqual(set(summary["task_rewards"]), {"task-1", "task-2", "task-3", "task-4"})
            self.assertEqual(summary["canonical_result_path"], str(source_result_path))

    def test_task_outcomes_counts_errored_task_as_zero(self) -> None:
        rewards, errors = task_outcomes(
            harbor_result({"task-1": 1.0}, errors=("task-2",)),
            ("task-1", "task-2"),
        )
        self.assertEqual(rewards, {"task-1": 1.0, "task-2": 0.0})
        self.assertEqual(errors, 1)

    def test_task_outcomes_rejects_missing_task(self) -> None:
        with self.assertRaises(ContinuationError):
            task_outcomes(harbor_result({"task-1": 1.0}), ("task-1", "task-2"))


if __name__ == "__main__":
    unittest.main()
