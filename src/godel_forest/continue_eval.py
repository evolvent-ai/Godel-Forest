"""Continue a diagnostic subset evaluation on the original trained checkpoint."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from godel_forest.multiagent.locks import file_lock


class ContinuationError(RuntimeError):
    """Raised when an attempt cannot be safely continued."""


Runner = Callable[..., subprocess.CompletedProcess[Any]]


@dataclass(frozen=True)
class ContinuationPlan:
    attempt_id: str
    attempt_run_id: str
    continuation_run_id: str
    workspace: Path
    repo_root: Path
    budget_state_path: Path
    lock_path: Path
    attempt_dir: Path
    source_run_dir: Path
    continuation_run_dir: Path
    source_result_path: Path
    model_path: str
    base_model: str
    harbor_dataset: str
    harbor_agent: str
    agent_config_profile: str
    n_concurrent: int
    n_attempts: int
    step_limit: int
    evaluated_task_ids: tuple[str, ...]
    remaining_task_ids: tuple[str, ...]
    full_task_ids: tuple[str, ...]
    owner_id: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_id")
    parser.add_argument("--workspace")
    parser.add_argument("--repo-root")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    workspace_raw = args.workspace or os.environ.get("RSIBENCH_DATA_WORK_DIR")
    repo_root_raw = args.repo_root or os.environ.get("RSIBENCH_DATA_REPO_ROOT")
    if not workspace_raw:
        raise SystemExit("workspace is required; use --workspace or RSIBENCH_DATA_WORK_DIR")
    if not repo_root_raw:
        raise SystemExit("repo root is required; use --repo-root or RSIBENCH_DATA_REPO_ROOT")
    owner_id = os.environ.get("DGB_ATTEMPT_OWNER", "default")
    try:
        return continue_evaluation(
            Path(workspace_raw),
            Path(repo_root_raw),
            args.attempt_id,
            owner_id=owner_id,
        )
    except ContinuationError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def continue_evaluation(
    workspace: Path,
    repo_root: Path,
    attempt_id: str,
    *,
    owner_id: str = "default",
    runner: Runner = subprocess.run,
) -> int:
    """Evaluate all tasks missing from a completed subset attempt."""
    workspace = Path(workspace).resolve()
    repo_root = Path(repo_root).resolve()
    plan = prepare_continuation(workspace, repo_root, attempt_id, owner_id=owner_id)
    if not plan.remaining_task_ids:
        print(f"attempt {attempt_id} already has full evaluation coverage")
        return 0

    try:
        plan.continuation_run_dir.mkdir(parents=True, exist_ok=False)
        task_ids_path = plan.continuation_run_dir / "eval_task_ids.json"
        _write_json(task_ids_path, list(plan.remaining_task_ids))
        started_at = int(time.time())
        _write_json(
            plan.continuation_run_dir / "continuation_manifest.json",
            {
                "attempt_id": plan.attempt_id,
                "attempt_run_id": plan.attempt_run_id,
                "continuation_run_id": plan.continuation_run_id,
                "model_path": plan.model_path,
                "source_result_path": str(plan.source_result_path),
                "evaluated_task_ids": list(plan.evaluated_task_ids),
                "remaining_task_ids": list(plan.remaining_task_ids),
                "status": "running",
                "started_at": started_at,
            },
        )
        command = evaluator_command(plan, task_ids_path)
        result = runner(command, cwd=str(repo_root), check=False)
    except BaseException:
        release_continuation_reservation(plan)
        raise
    if result.returncode != 0:
        try:
            _finish_manifest(plan, status="failed", return_code=int(result.returncode))
        finally:
            release_continuation_reservation(plan)
        return int(result.returncode)

    try:
        promote_completed_continuation(plan)
    except BaseException:
        release_continuation_reservation(plan)
        raise
    _finish_manifest(plan, status="completed", return_code=0)
    print(
        f"attempt {attempt_id} now has full evaluation coverage on its original checkpoint"
    )
    return 0


def prepare_continuation(
    workspace: Path,
    repo_root: Path,
    attempt_id: str,
    *,
    owner_id: str,
) -> ContinuationPlan:
    budget_state_path = workspace.parent / "runner_state" / "budget_state.json"
    lock_path = budget_state_path.with_name("budget_state.lock")
    attempt_dir = workspace / "attempts" / attempt_id
    if not attempt_dir.is_dir():
        raise ContinuationError(f"unknown attempt: {attempt_id}")
    if owner_id != "default" and not attempt_id.startswith(f"{owner_id}_"):
        raise ContinuationError(
            f"attempt {attempt_id} is not owned by worker {owner_id}; use this worker's prefix"
        )

    with file_lock(lock_path):
        state = _read_json(budget_state_path)
        iteration = _find_iteration(state, attempt_id)
        if iteration.get("status") != "completed" or iteration.get("return_code") != 0:
            raise ContinuationError(f"attempt is not a completed successful evaluation: {attempt_id}")
        if iteration.get("evaluation_scope") != "subset":
            if iteration.get("evaluation_scope", "full") == "full":
                return _already_full_plan(
                    workspace, repo_root, budget_state_path, lock_path, attempt_dir, iteration, owner_id
                )
            raise ContinuationError(f"attempt is not a diagnostic subset: {attempt_id}")
        if state.get("attempt_admissions_closed"):
            raise ContinuationError("new evaluations are disabled while the manager is finalizing")
        _require_remaining_budget(state, owner_id=owner_id)
        active = [item for item in state.get("active_attempts", []) if isinstance(item, dict)]
        if any(item.get("owner_id") == owner_id for item in active):
            raise ContinuationError(f"owner already has an active attempt: {owner_id}")
        if any(item.get("attempt_id") == attempt_id for item in active):
            raise ContinuationError(f"attempt is already active: {attempt_id}")

        attempt_run_id = _required_string(iteration, "attempt_run_id")
        source_run_dir = repo_root / "artifacts" / "runs" / attempt_run_id
        metadata = _read_json(source_run_dir / "tinker_train_metadata.json")
        model_path = _required_string(metadata, "model_path")
        source_result_path = Path(_required_string(iteration, "harbor_result_path"))
        source_cost_path = _source_cost_path(iteration, source_run_dir)
        source_cost = _read_json(source_cost_path)
        harbor_dataset = _required_string(source_cost, "harbor_dataset")
        full_task_ids = tuple(_dataset_task_ids(repo_root, harbor_dataset))
        evaluated_task_ids = tuple(_string_list(iteration.get("evaluation_task_ids")))
        if not evaluated_task_ids:
            raise ContinuationError(f"subset attempt has no recorded task IDs: {attempt_id}")
        unknown = sorted(set(evaluated_task_ids) - set(full_task_ids))
        if unknown:
            raise ContinuationError(f"subset contains tasks outside the configured dataset: {unknown}")
        remaining_task_ids = tuple(task for task in full_task_ids if task not in set(evaluated_task_ids))
        if not remaining_task_ids:
            iteration["evaluation_scope"] = "full"
            iteration["evaluation_n_tasks"] = len(full_task_ids)
            iteration["evaluation_task_ids"] = list(full_task_ids)
            iteration["continued_from_subset"] = True
            iteration["updated_at"] = int(time.time())
            _recompute_budget(state)
            _recompute_best(state)
            _atomic_write_json(budget_state_path, state)
            return _already_full_plan(
                workspace, repo_root, budget_state_path, lock_path, attempt_dir, iteration, owner_id
            )

        base_model = _required_string(state, "target_model")
        harbor_agent = _required_string(source_cost, "harbor_agent")
        agent_config_profile = _required_string(source_cost, "agent_config_profile")
        n_concurrent = _positive_int(source_cost, "n_concurrent")
        n_attempts = _positive_int(source_cost, "n_attempts")
        if n_attempts != 1:
            raise ContinuationError("checkpoint continuation currently requires n_attempts=1")
        step_limit = _positive_int(source_cost, "step_limit")
        continuation_run_id = f"{attempt_run_id}-continue-{time.time_ns()}"
        continuation_run_dir = repo_root / "artifacts" / "runs" / continuation_run_id
        reservation = {
            "attempt_id": attempt_id,
            "attempt_run_id": continuation_run_id,
            "continuation_of_attempt_run_id": attempt_run_id,
            "owner_id": owner_id,
            "pid": os.getpid(),
            "reserved_tinker_usd": 0.0,
            "started_at": int(time.time()),
            "evaluation_scope": "continuation",
            "evaluation_task_ids": list(remaining_task_ids),
            "evaluation_n_tasks": len(remaining_task_ids),
        }
        active.append(reservation)
        state["active_attempts"] = active
        _recompute_budget(state)
        _atomic_write_json(budget_state_path, state)

    return ContinuationPlan(
        attempt_id=attempt_id,
        attempt_run_id=attempt_run_id,
        continuation_run_id=continuation_run_id,
        workspace=workspace,
        repo_root=repo_root,
        budget_state_path=budget_state_path,
        lock_path=lock_path,
        attempt_dir=attempt_dir,
        source_run_dir=source_run_dir,
        continuation_run_dir=continuation_run_dir,
        source_result_path=source_result_path,
        model_path=model_path,
        base_model=base_model,
        harbor_dataset=harbor_dataset,
        harbor_agent=harbor_agent,
        agent_config_profile=agent_config_profile,
        n_concurrent=n_concurrent,
        n_attempts=n_attempts,
        step_limit=step_limit,
        evaluated_task_ids=evaluated_task_ids,
        remaining_task_ids=remaining_task_ids,
        full_task_ids=full_task_ids,
        owner_id=owner_id,
    )


def evaluator_command(plan: ContinuationPlan, task_ids_path: Path) -> list[str]:
    infra_retries = _infra_retries()
    return [
        sys.executable,
        "-m",
        "backend.e2b_eval",
        "--model-path",
        plan.model_path,
        "--run-dir",
        str(plan.continuation_run_dir),
        "--base-model",
        plan.base_model,
        "--harbor-dataset",
        plan.harbor_dataset,
        "--harbor-agent",
        plan.harbor_agent,
        "--agent-config-profile",
        plan.agent_config_profile,
        "--n-tasks",
        str(len(plan.remaining_task_ids)),
        "--n-concurrent",
        str(plan.n_concurrent),
        "--n-attempts",
        str(plan.n_attempts),
        "--infra-retries",
        str(infra_retries),
        "--step-limit",
        str(plan.step_limit),
        "--task-ids-file",
        str(task_ids_path),
    ]


def _infra_retries() -> int:
    raw_value = os.environ.get("DGB_INFRA_RETRIES", "0")
    try:
        retries = int(raw_value)
    except ValueError as exc:
        raise ContinuationError(
            f"DGB_INFRA_RETRIES must be a non-negative integer, got {raw_value!r}"
        ) from exc
    if retries < 0:
        raise ContinuationError("DGB_INFRA_RETRIES must be a non-negative integer")
    return retries


def promote_completed_continuation(plan: ContinuationPlan) -> dict[str, Any]:
    continuation_result_path = _latest_harbor_result(plan.continuation_run_dir)
    continuation_cost_path = plan.continuation_run_dir / "tinker_cost_estimate.json"
    continuation_cost = _read_json(continuation_cost_path)
    initial_result = _read_json(plan.source_result_path)
    initial_rewards, initial_errors = task_outcomes(
        initial_result, plan.evaluated_task_ids
    )
    continuation_rewards, continuation_errors = task_outcomes(
        _read_json(continuation_result_path), plan.remaining_task_ids
    )
    task_rewards = {**initial_rewards, **continuation_rewards}
    if set(task_rewards) != set(plan.full_task_ids):
        missing = sorted(set(plan.full_task_ids) - set(task_rewards))
        raise ContinuationError(f"continued evaluation did not cover all configured tasks: {missing}")
    score = sum(task_rewards.values()) / len(plan.full_task_ids)
    summary_path = plan.attempt_dir / "continued_eval_summary.json"

    with file_lock(plan.lock_path):
        state = _read_json(plan.budget_state_path)
        iteration = _find_iteration(state, plan.attempt_id)
        if iteration.get("attempt_run_id") != plan.attempt_run_id:
            raise ContinuationError("attempt_run_id changed while continuation was running")
        if iteration.get("evaluation_scope") != "subset":
            raise ContinuationError("attempt evaluation scope changed while continuation was running")
        source_cost_path = _source_cost_path(iteration, plan.source_run_dir)
        source_cost = _read_json(source_cost_path)
        continuation_sample_usd = _number(continuation_cost.get("estimated_total_usd"))
        canonical_result_path, canonical_cost_detail = _write_canonical_projection(
            plan,
            initial_result=initial_result,
            source_cost_path=source_cost_path,
            source_cost=source_cost,
            continuation_cost=continuation_cost,
            task_rewards=task_rewards,
            harbor_errors=initial_errors + continuation_errors,
        )
        shards = [
            {
                "kind": "initial_subset",
                "run_id": plan.attempt_run_id,
                "result_path": str(_initial_subset_artifact_path(plan.source_result_path)),
                "task_ids": list(plan.evaluated_task_ids),
                "n_tasks": len(plan.evaluated_task_ids),
                "harbor_score": sum(initial_rewards.values()) / len(initial_rewards),
                "harbor_errors": initial_errors,
            },
            {
                "kind": "continuation",
                "run_id": plan.continuation_run_id,
                "result_path": str(continuation_result_path),
                "task_ids": list(plan.remaining_task_ids),
                "n_tasks": len(plan.remaining_task_ids),
                "harbor_score": sum(continuation_rewards.values()) / len(continuation_rewards),
                "harbor_errors": continuation_errors,
                "estimated_sample_usd": continuation_sample_usd,
                "sample_cost_path": str(continuation_cost_path),
            },
        ]
        summary = {
            "attempt_id": plan.attempt_id,
            "attempt_run_id": plan.attempt_run_id,
            "model_path": plan.model_path,
            "evaluation_scope": "full",
            "evaluation_n_tasks": len(plan.full_task_ids),
            "evaluation_task_ids": list(plan.full_task_ids),
            "harbor_score": score,
            "harbor_errors": initial_errors + continuation_errors,
            "task_rewards": task_rewards,
            "evaluation_shards": shards,
            "canonical_result_path": str(canonical_result_path),
            "canonical_cost_path": str(plan.source_run_dir / "tinker_cost_estimate.json"),
            "updated_at": int(time.time()),
        }
        _atomic_write_json(summary_path, summary)
        iteration.update(
            {
                "evaluation_scope": "full",
                "evaluation_task_ids": list(plan.full_task_ids),
                "evaluation_n_tasks": len(plan.full_task_ids),
                "evaluation_shards": shards,
                "continued_from_subset": True,
                "harbor_score": score,
                "harbor_errors": initial_errors + continuation_errors,
                "harbor_result_path": str(canonical_result_path),
                "estimated_tinker_usd": canonical_cost_detail["estimated_total_usd"],
                "tinker_cost_detail": canonical_cost_detail,
                "updated_at": int(time.time()),
            }
        )
        state["active_attempts"] = [
            item
            for item in state.get("active_attempts", [])
            if not (
                isinstance(item, dict)
                and item.get("attempt_run_id") == plan.continuation_run_id
            )
        ]
        _recompute_budget(state)
        _recompute_best(state)
        _atomic_write_json(plan.budget_state_path, state)
    return summary


def task_outcomes(result: dict[str, Any], expected_task_ids: Sequence[str]) -> tuple[dict[str, float], int]:
    expected = tuple(expected_task_ids)
    rewards: dict[str, float] = {}
    error_tasks: set[str] = set()
    stats = result.get("stats")
    evals = stats.get("evals") if isinstance(stats, dict) else None
    if not isinstance(evals, dict):
        raise ContinuationError("Harbor result has no stats.evals mapping")
    error_count = 0
    for eval_result in evals.values():
        if not isinstance(eval_result, dict):
            continue
        value = eval_result.get("n_errors")
        if isinstance(value, int) and not isinstance(value, bool):
            error_count += value
        reward_stats = eval_result.get("reward_stats")
        reward_map = reward_stats.get("reward") if isinstance(reward_stats, dict) else None
        if isinstance(reward_map, dict):
            for raw_reward, trial_ids in reward_map.items():
                try:
                    reward = float(raw_reward)
                except (TypeError, ValueError):
                    continue
                if not isinstance(trial_ids, list):
                    continue
                for trial_id in trial_ids:
                    task_id = _match_task_id(trial_id, expected)
                    if task_id in rewards:
                        raise ContinuationError(f"duplicate Harbor reward for task: {task_id}")
                    rewards[task_id] = reward
        for trial_id in _nested_strings(eval_result.get("exception_stats")):
            try:
                error_tasks.add(_match_task_id(trial_id, expected))
            except ContinuationError:
                continue
    for task_id in error_tasks:
        rewards.setdefault(task_id, 0.0)
    missing = sorted(set(expected) - set(rewards))
    if missing:
        raise ContinuationError(f"Harbor result is missing task outcomes: {missing}")
    return rewards, error_count


def release_continuation_reservation(plan: ContinuationPlan) -> None:
    with file_lock(plan.lock_path):
        state = _read_json(plan.budget_state_path)
        state["active_attempts"] = [
            item
            for item in state.get("active_attempts", [])
            if not (
                isinstance(item, dict)
                and item.get("attempt_run_id") == plan.continuation_run_id
            )
        ]
        _recompute_budget(state)
        _atomic_write_json(plan.budget_state_path, state)


def _already_full_plan(
    workspace: Path,
    repo_root: Path,
    budget_state_path: Path,
    lock_path: Path,
    attempt_dir: Path,
    iteration: dict[str, Any],
    owner_id: str,
) -> ContinuationPlan:
    attempt_run_id = _required_string(iteration, "attempt_run_id")
    source_run_dir = repo_root / "artifacts" / "runs" / attempt_run_id
    return ContinuationPlan(
        attempt_id=_required_string(iteration, "attempt_id"),
        attempt_run_id=attempt_run_id,
        continuation_run_id="",
        workspace=workspace,
        repo_root=repo_root,
        budget_state_path=budget_state_path,
        lock_path=lock_path,
        attempt_dir=attempt_dir,
        source_run_dir=source_run_dir,
        continuation_run_dir=source_run_dir,
        source_result_path=Path(str(iteration.get("harbor_result_path", ""))),
        model_path="",
        base_model="",
        harbor_dataset="",
        harbor_agent="",
        agent_config_profile="",
        n_concurrent=1,
        n_attempts=1,
        step_limit=1,
        evaluated_task_ids=tuple(_string_list(iteration.get("evaluation_task_ids"))),
        remaining_task_ids=(),
        full_task_ids=tuple(_string_list(iteration.get("evaluation_task_ids"))),
        owner_id=owner_id,
    )


def _finish_manifest(plan: ContinuationPlan, *, status: str, return_code: int) -> None:
    path = plan.continuation_run_dir / "continuation_manifest.json"
    data = _read_json(path)
    data.update(
        {
            "status": status,
            "return_code": return_code,
            "completed_at": int(time.time()),
        }
    )
    _write_json(path, data)


def _write_canonical_projection(
    plan: ContinuationPlan,
    *,
    initial_result: dict[str, Any],
    source_cost_path: Path,
    source_cost: dict[str, Any],
    continuation_cost: dict[str, Any],
    task_rewards: dict[str, float],
    harbor_errors: int,
) -> tuple[Path, dict[str, Any]]:
    """Preserve shard artifacts and write legacy-compatible aggregate artifacts."""
    initial_result_path = _initial_subset_artifact_path(plan.source_result_path)
    initial_cost_path = _initial_subset_artifact_path(source_cost_path)
    if not initial_result_path.exists():
        _atomic_write_json(initial_result_path, initial_result)
    if not initial_cost_path.exists():
        _atomic_write_json(initial_cost_path, source_cost)

    canonical_cost_path = plan.source_run_dir / "tinker_cost_estimate.json"
    canonical_cost = dict(source_cost)
    canonical_cost.update(
        {
            "prompt_tokens": int(_number(source_cost.get("prompt_tokens")))
            + int(_number(continuation_cost.get("prompt_tokens"))),
            "completion_tokens": int(_number(source_cost.get("completion_tokens")))
            + int(_number(continuation_cost.get("completion_tokens"))),
            "estimated_total_usd": _number(source_cost.get("estimated_total_usd"))
            + _number(continuation_cost.get("estimated_total_usd")),
            "task_ids": list(plan.full_task_ids),
        }
    )
    _atomic_write_json(canonical_cost_path, canonical_cost)

    canonical_result_path = plan.source_result_path
    _atomic_write_json(
        canonical_result_path,
        _canonical_harbor_result(task_rewards, harbor_errors),
    )
    return canonical_result_path, _canonical_cost_detail(plan, canonical_cost)


def _initial_subset_artifact_path(path: Path) -> Path:
    return path.with_name(f"initial_subset_{path.name}")


def _canonical_harbor_result(task_rewards: dict[str, float], harbor_errors: int) -> dict[str, Any]:
    rewards: dict[str, list[str]] = {}
    for task_id, reward in sorted(task_rewards.items()):
        rewards.setdefault(str(float(reward)), []).append(task_id)
    total_tasks = len(task_rewards)
    score = sum(task_rewards.values()) / total_tasks if total_tasks else None
    return {
        "kind": "continued_evaluation_aggregate",
        "n_total_trials": total_tasks,
        "stats": {
            "n_completed_trials": total_tasks,
            "n_errored_trials": harbor_errors,
            "evals": {
                "aggregate": {
                    "n_trials": total_tasks,
                    "n_errors": harbor_errors,
                    "metrics": [{"mean": score}],
                    "reward_stats": {"reward": rewards},
                }
            },
        },
    }


def _canonical_cost_detail(plan: ContinuationPlan, canonical_cost: dict[str, Any]) -> dict[str, Any]:
    train_metadata_path = plan.source_run_dir / "tinker_train_metadata.json"
    train_metadata = _read_json(train_metadata_path)
    prompt_tokens = int(_number(canonical_cost.get("prompt_tokens")))
    completion_tokens = int(_number(canonical_cost.get("completion_tokens")))
    sample_usd = _number(canonical_cost.get("estimated_total_usd"))
    train_usd = _number(train_metadata.get("estimated_train_usd"))
    detail = {
        "train_tokens": int(_number(train_metadata.get("processed_train_tokens"))),
        "estimated_train_usd": train_usd,
        "sample_prompt_tokens": prompt_tokens,
        "sample_completion_tokens": completion_tokens,
        "sample_total_tokens": prompt_tokens + completion_tokens,
        "estimated_sample_usd": sample_usd,
        "estimated_total_usd": train_usd + sample_usd,
        "train_metadata_path": str(train_metadata_path),
        "sample_cost_path": str(plan.source_run_dir / "tinker_cost_estimate.json"),
        "task_ids": list(plan.full_task_ids),
    }
    return detail


def _source_cost_path(iteration: dict[str, Any], source_run_dir: Path) -> Path:
    detail = iteration.get("tinker_cost_detail")
    if isinstance(detail, dict) and isinstance(detail.get("sample_cost_path"), str):
        return Path(detail["sample_cost_path"])
    return source_run_dir / "tinker_cost_estimate.json"


def _latest_harbor_result(run_dir: Path) -> Path:
    candidates = sorted((run_dir / "harbor" / "jobs").glob("*/result.json"))
    if not candidates:
        raise ContinuationError(f"continued evaluation produced no Harbor result: {run_dir}")
    return candidates[-1]


def _dataset_task_ids(repo_root: Path, harbor_dataset: str) -> list[str]:
    dataset = Path(harbor_dataset)
    if not dataset.is_absolute():
        dataset = repo_root / dataset
    if not dataset.is_dir():
        raise ContinuationError(f"Harbor dataset directory does not exist: {dataset}")
    task_ids = sorted(
        child.name
        for child in dataset.iterdir()
        if child.is_dir() and (child / "task.toml").is_file()
    )
    if not task_ids:
        raise ContinuationError(f"Harbor dataset contains no task directories: {dataset}")
    return task_ids


def _find_iteration(state: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in state.get("iterations", [])
        if isinstance(item, dict) and item.get("attempt_id") == attempt_id
    ]
    if not matches:
        raise ContinuationError(f"attempt was not recorded by run_attempt.sh: {attempt_id}")
    return matches[-1]


def _match_task_id(raw_trial_id: Any, expected: Sequence[str]) -> str:
    if not isinstance(raw_trial_id, str):
        raise ContinuationError(f"invalid Harbor trial id: {raw_trial_id!r}")
    matches = [
        task_id
        for task_id in expected
        if raw_trial_id == task_id or raw_trial_id.startswith(f"{task_id}__")
    ]
    if len(matches) != 1:
        raise ContinuationError(f"could not map Harbor trial to one task: {raw_trial_id}")
    return matches[0]


def _nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _nested_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _nested_strings(child)]
    return []


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ContinuationError(f"missing required {key}")
    return value


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContinuationError(f"missing or invalid {key}")
    return value


def _require_remaining_budget(state: dict[str, Any], *, owner_id: str = "default") -> None:
    now = int(time.time())
    started_at = int(state.get("started_at", now))
    budget = state.get("budget") if isinstance(state.get("budget"), dict) else {}
    wall_budget = int(_number(budget.get("wall_time_budget_sec")))
    if wall_budget - max(0, now - started_at) <= 0:
        raise ContinuationError("wall-time budget is exhausted")
    remaining_cost = state.get("remaining_tinker_cost_usd")
    if remaining_cost is not None and _number(remaining_cost) <= 0:
        raise ContinuationError("Tinker cost budget is exhausted")
    budget = state.get("budget") if isinstance(state.get("budget"), dict) else {}
    per_agent_budget = _number(budget.get("per_agent_cost_budget_usd"))
    if per_agent_budget > 0:
        agent_spend = state.get("agent_spend")
        spent = _number(agent_spend.get(owner_id)) if isinstance(agent_spend, dict) else 0.0
        if spent >= per_agent_budget:
            raise ContinuationError(f"agent cost budget is exhausted for owner {owner_id}")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContinuationError(f"required JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContinuationError(f"invalid JSON file: {path}") from exc
    if not isinstance(data, dict):
        raise ContinuationError(f"JSON root must be an object: {path}")
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(raw_tmp_path)
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _recompute_budget(state: dict[str, Any]) -> None:
    iterations = [item for item in state.get("iterations", []) if isinstance(item, dict)]
    active = [item for item in state.get("active_attempts", []) if isinstance(item, dict)]
    used = sum(_number(item.get("estimated_tinker_usd")) for item in iterations)
    reserved = sum(_number(item.get("reserved_tinker_usd")) for item in active)
    now = int(time.time())
    started_at = int(state.get("started_at", now))
    budget = state.get("budget") if isinstance(state.get("budget"), dict) else {}
    wall_budget = int(_number(budget.get("wall_time_budget_sec")))
    cost_budget = _number(budget.get("tinker_cost_budget_usd"))
    state["estimated_tinker_usd_used"] = used
    per_agent_budget = _number(budget.get("per_agent_cost_budget_usd"))
    if per_agent_budget > 0:
        state["agent_spend"] = {
            owner_id: sum(
                _number(item.get("estimated_tinker_usd"))
                for item in iterations
                if item.get("owner_id") == owner_id
            )
            for owner_id in {
                item.get("owner_id")
                for item in iterations
                if isinstance(item.get("owner_id"), str) and item.get("owner_id")
            }
        }
    state["reserved_tinker_usd"] = reserved
    state["elapsed_sec"] = max(0, now - started_at)
    state["remaining_wall_time_sec"] = max(0, wall_budget - state["elapsed_sec"])
    state["remaining_tinker_cost_usd"] = max(0.0, cost_budget - used - reserved)


def _recompute_best(state: dict[str, Any]) -> None:
    best: dict[str, Any] | None = None
    for item in state.get("iterations", []):
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        if item.get("evaluation_scope") == "subset":
            continue
        score = item.get("harbor_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        candidate = {
            "attempt_id": item.get("attempt_id"),
            "attempt_run_id": item.get("attempt_run_id"),
            "harbor_score": float(score),
        }
        if best is None or candidate["harbor_score"] > best["harbor_score"]:
            best = candidate
    state["best_attempt"] = best


if __name__ == "__main__":
    raise SystemExit(main())
