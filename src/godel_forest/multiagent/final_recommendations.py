"""Read and write per-agent terminal recommendations."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .leaderboard import read_budget_state
from .locks import file_lock


@dataclass(frozen=True)
class TerminalRecommendation:
    agent_id: str
    decision: str
    source: str
    reason: str
    reason_type: str
    attempt_id: str | None = None
    score: float | None = None
    evaluation_scope: str | None = None
    error_since: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "decision": self.decision,
            "source": self.source,
            "reason": self.reason,
            "reason_type": self.reason_type,
            "attempt_id": self.attempt_id,
            "score": self.score,
            "evaluation_scope": self.evaluation_scope,
            "error_since": self.error_since,
            "last_error": self.last_error,
        }


def recommendation_path(public_dir: Path, agent_id: str) -> Path:
    return Path(public_dir) / "final_recommendations" / f"{agent_id}.json"


def write_terminal_recommendation(public_dir: Path, recommendation: TerminalRecommendation) -> None:
    path = recommendation_path(public_dir, recommendation.agent_id)
    _atomic_write(path, recommendation.to_dict())


def read_terminal_recommendations(
    public_dir: Path,
    agent_ids: tuple[str, ...] | list[str],
    budget_state_path: Path,
    lock_path: Path,
) -> dict[str, TerminalRecommendation]:
    with file_lock(lock_path):
        state = read_budget_state(budget_state_path)
    recommendations: dict[str, TerminalRecommendation] = {}
    for agent_id in agent_ids:
        path = recommendation_path(public_dir, agent_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        recommendation = _parse(payload)
        if recommendation is not None and _is_valid(recommendation, agent_id, state):
            recommendations[agent_id] = recommendation
    return recommendations


def _parse(payload: Any) -> TerminalRecommendation | None:
    if not isinstance(payload, dict):
        return None
    agent_id = payload.get("agent_id")
    decision = payload.get("decision")
    source = payload.get("source")
    reason = payload.get("reason")
    reason_type = payload.get("reason_type")
    if not all(isinstance(value, str) and value for value in (agent_id, decision, source, reason, reason_type)):
        return None
    attempt_id = payload.get("attempt_id")
    if attempt_id is not None and not isinstance(attempt_id, str):
        return None
    score = payload.get("score")
    if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool)):
        return None
    evaluation_scope = payload.get("evaluation_scope")
    if evaluation_scope is not None and not isinstance(evaluation_scope, str):
        return None
    error_since = payload.get("error_since")
    if error_since is not None and (not isinstance(error_since, (int, float)) or isinstance(error_since, bool)):
        return None
    last_error = payload.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        return None
    return TerminalRecommendation(
        agent_id=agent_id,
        decision=decision,
        source=source,
        reason=reason,
        reason_type=reason_type,
        attempt_id=attempt_id,
        score=float(score) if score is not None else None,
        evaluation_scope=evaluation_scope,
        error_since=float(error_since) if error_since is not None else None,
        last_error=last_error,
    )


def _is_valid(recommendation: TerminalRecommendation, expected_agent_id: str, state: dict[str, Any]) -> bool:
    if recommendation.agent_id != expected_agent_id or recommendation.decision != "submit":
        return False
    if recommendation.source == "manager" and recommendation.reason_type == "agent_error_timeout":
        return _references_completed_full_attempt(recommendation, state, allow_missing=True)
    if recommendation.source != "agent" or recommendation.reason_type != "agent_decision":
        return False
    return _references_completed_full_attempt(recommendation, state, allow_missing=False)


def _references_completed_full_attempt(
    recommendation: TerminalRecommendation,
    state: dict[str, Any],
    *,
    allow_missing: bool,
) -> bool:
    if recommendation.attempt_id is None:
        return allow_missing
    if recommendation.evaluation_scope != "full" or recommendation.score is None:
        return False
    for item in state.get("iterations", []):
        if not isinstance(item, dict) or item.get("attempt_id") != recommendation.attempt_id:
            continue
        if item.get("status") != "completed":
            return False
        scope = item.get("evaluation_scope", "full")
        score = item.get("harbor_score")
        return (
            scope == "full"
            and isinstance(score, (int, float))
            and not isinstance(score, bool)
            and abs(float(score) - recommendation.score) <= 1e-9
        )
    return False


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(raw_tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
