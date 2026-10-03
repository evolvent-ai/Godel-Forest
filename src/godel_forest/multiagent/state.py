"""Shared RSIBench-Data attempt-state helpers for the multi-agent manager."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .locks import file_lock


def active_attempts(budget_state_path: Path, lock_path: Path) -> list[dict[str, Any]]:
    with file_lock(lock_path):
        state = _read_state(budget_state_path)
        return [
            dict(item)
            for item in state.get("active_attempts", [])
            if isinstance(item, dict)
        ]


def set_attempt_admissions(
    budget_state_path: Path,
    lock_path: Path,
    *,
    closed: bool,
) -> None:
    with file_lock(lock_path):
        state = _read_state(budget_state_path)
        state["attempt_admissions_closed"] = closed
        _atomic_write_state(budget_state_path, state)


def prune_stale_active_attempts(
    budget_state_path: Path,
    lock_path: Path,
) -> list[dict[str, Any]]:
    with file_lock(lock_path):
        state = _read_state(budget_state_path)
        raw = state.get("active_attempts", [])
        active = [
            dict(item)
            for item in raw
            if isinstance(item, dict) and _attempt_process_exists(item)
        ]
        raw_count = len(raw) if isinstance(raw, list) else 0
        if not isinstance(raw, list) or len(active) != raw_count:
            state["active_attempts"] = active
            _recompute_reserved_budget(state)
            _atomic_write_state(budget_state_path, state)
        return active


def _attempt_process_exists(item: dict[str, Any]) -> bool:
    pid = item.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _recompute_reserved_budget(state: dict[str, Any]) -> None:
    active = [item for item in state.get("active_attempts", []) if isinstance(item, dict)]
    reserved = sum(_number(item.get("reserved_tinker_usd")) for item in active)
    used = _number(state.get("estimated_tinker_usd_used"))
    cost_budget = _number(state.get("budget", {}).get("tinker_cost_budget_usd"))
    state["reserved_tinker_usd"] = reserved
    state["remaining_tinker_cost_usd"] = max(0.0, cost_budget - used - reserved)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _atomic_write_state(path: Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(raw_tmp_path)
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
