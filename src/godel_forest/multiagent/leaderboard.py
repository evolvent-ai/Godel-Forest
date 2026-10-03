"""Render shared leaderboard data from RSIBench-Data budget_state.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .locks import file_lock


@dataclass(frozen=True)
class AttemptSummary:
    attempt_id: str
    attempt_run_id: str | None
    status: str
    harbor_score: float | None
    return_code: int | None
    estimated_tinker_usd: float | None
    harbor_result_path: str | None
    evaluation_scope: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_run_id": self.attempt_run_id,
            "status": self.status,
            "harbor_score": self.harbor_score,
            "return_code": self.return_code,
            "estimated_tinker_usd": self.estimated_tinker_usd,
            "harbor_result_path": self.harbor_result_path,
            "evaluation_scope": self.evaluation_scope,
        }


@dataclass(frozen=True)
class BaselineSummary:
    model: str
    harbor_score: float
    evaluation_scope: str
    solved_task_ids: tuple[str, ...]
    unsolved_task_ids: tuple[str, ...]
    harbor_result_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "harbor_score": self.harbor_score,
            "evaluation_scope": self.evaluation_scope,
            "solved_task_ids": list(self.solved_task_ids),
            "unsolved_task_ids": list(self.unsolved_task_ids),
            "harbor_result_path": self.harbor_result_path,
            "source": "precomputed_base_model_eval",
        }


def read_budget_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def completed_attempts(state: dict[str, Any]) -> list[AttemptSummary]:
    """Return completed full-set attempts sorted by descending score."""
    attempts: list[AttemptSummary] = []
    for item in state.get("iterations", []):
        summary = _summary_from_iteration(item)
        if summary is None:
            continue
        if (
            summary.status == "completed"
            and summary.harbor_score is not None
            and summary.evaluation_scope == "full"
        ):
            attempts.append(summary)
    attempts.sort(key=lambda a: a.harbor_score if a.harbor_score is not None else float("-inf"), reverse=True)
    return attempts


def best_completed_attempt(state: dict[str, Any]) -> AttemptSummary | None:
    return next(iter(completed_attempts(state)), None)


def render_leaderboard(budget_state_path: Path, output_path: Path, lock_path: Path | None = None) -> dict[str, Any]:
    """Render public leaderboard JSON, locking budget reads when a lock is supplied."""
    if lock_path is not None:
        with file_lock(lock_path):
            state = read_budget_state(budget_state_path)
    else:
        state = read_budget_state(budget_state_path)
    attempts = completed_attempts(state)
    best = best_completed_attempt(state)
    baseline = read_base_model_baseline(state, output_path)
    payload = {
        "run_id": state.get("run_id"),
        "remaining_tinker_cost_usd": state.get("remaining_tinker_cost_usd"),
        "remaining_wall_time_sec": state.get("remaining_wall_time_sec"),
        "baseline": baseline.to_dict() if baseline else None,
        "attempts": [a.to_dict() for a in attempts],
        "best": best.to_dict() if best else None,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def read_base_model_baseline(state: dict[str, Any], output_path: Path) -> BaselineSummary | None:
    target_model = state.get("target_model")
    target_benchmark = state.get("target_benchmark")
    if not isinstance(target_model, str) or not target_model:
        return None
    if not isinstance(target_benchmark, str) or not target_benchmark:
        return None
    workspace_root = _workspace_root(output_path)
    if workspace_root is None:
        return None
    benchmark_dir = _benchmark_dir(workspace_root / "benchmarks", target_benchmark)
    if benchmark_dir is None:
        return None
    expected_trials = _expected_official_trials(benchmark_dir / "spec.json")
    if expected_trials is None:
        return None
    for result_path in sorted((benchmark_dir / "runs").glob("*/harbor/jobs/*/result.json")):
        result = read_budget_state(result_path)
        baseline = _baseline_from_result(
            result,
            target_model=target_model,
            expected_trials=expected_trials,
            result_path=result_path,
            workspace_root=workspace_root,
        )
        if baseline is not None:
            return baseline
    return None


def _summary_from_iteration(item: Any) -> AttemptSummary | None:
    if not isinstance(item, dict):
        return None
    attempt_id = item.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        return None
    score = item.get("harbor_score")
    score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    return_code = item.get("return_code")
    return_code = return_code if isinstance(return_code, int) and not isinstance(return_code, bool) else None
    cost = item.get("estimated_tinker_usd")
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    attempt_run_id = item.get("attempt_run_id")
    harbor_result_path = item.get("harbor_result_path")
    evaluation_scope = item.get("evaluation_scope")
    return AttemptSummary(
        attempt_id=attempt_id,
        attempt_run_id=attempt_run_id if isinstance(attempt_run_id, str) else None,
        status=str(item.get("status", "")),
        harbor_score=score,
        return_code=return_code,
        estimated_tinker_usd=cost,
        harbor_result_path=harbor_result_path if isinstance(harbor_result_path, str) else None,
        evaluation_scope=evaluation_scope if isinstance(evaluation_scope, str) else "full",
    )


def _workspace_root(output_path: Path) -> Path | None:
    for candidate in (output_path.parent, *output_path.parents):
        if (candidate / "benchmarks").is_dir():
            return candidate
    return None


def _benchmark_dir(benchmarks_root: Path, target_benchmark: str) -> Path | None:
    for spec_path in sorted(benchmarks_root.glob("*/spec.json")):
        spec = read_budget_state(spec_path)
        if spec.get("target_benchmark") == target_benchmark:
            return spec_path.parent
    return None


def _expected_official_trials(spec_path: Path) -> int | None:
    spec = read_budget_state(spec_path)
    official_eval = spec.get("official_eval")
    if not isinstance(official_eval, dict):
        return None
    n_tasks = official_eval.get("n_tasks")
    n_attempts = official_eval.get("n_attempts", 1)
    if not isinstance(n_tasks, int) or isinstance(n_tasks, bool) or n_tasks < 1:
        return None
    if not isinstance(n_attempts, int) or isinstance(n_attempts, bool) or n_attempts < 1:
        return None
    return n_tasks * n_attempts


def _baseline_from_result(
    result: dict[str, Any],
    *,
    target_model: str,
    expected_trials: int,
    result_path: Path,
    workspace_root: Path,
) -> BaselineSummary | None:
    stats = result.get("stats")
    evals = stats.get("evals") if isinstance(stats, dict) else None
    if not isinstance(evals, dict):
        return None
    model_marker = f"__{target_model}__"
    for eval_name, eval_result in evals.items():
        if not isinstance(eval_name, str) or model_marker not in eval_name:
            continue
        if not isinstance(eval_result, dict) or eval_result.get("n_trials") != expected_trials:
            continue
        metrics = eval_result.get("metrics")
        metric = metrics[0] if isinstance(metrics, list) and metrics else None
        score = metric.get("mean") if isinstance(metric, dict) else None
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        reward_stats = eval_result.get("reward_stats")
        rewards = reward_stats.get("reward") if isinstance(reward_stats, dict) else None
        solved = _task_ids(rewards.get("1.0")) if isinstance(rewards, dict) else ()
        unsolved = _task_ids(rewards.get("0.0")) if isinstance(rewards, dict) else ()
        try:
            relative_result_path = result_path.relative_to(workspace_root)
        except ValueError:
            relative_result_path = result_path
        return BaselineSummary(
            model=target_model,
            harbor_score=float(score),
            evaluation_scope="full",
            solved_task_ids=solved,
            unsolved_task_ids=unsolved,
            harbor_result_path=str(relative_result_path),
        )
    return None


def _task_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)
