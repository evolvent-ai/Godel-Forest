"""Top-level manager loop for multi-agent orchestration."""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

from godel_forest.claude_driver import ClaudeDriver
from godel_forest.contract import WorkspaceContract
from .config import MultiAgentConfig, load_multiagent_config
from .finalizer import BestAttemptFinalizer, FinalizeResult
from .final_recommendations import (
    TerminalRecommendation,
    read_terminal_recommendations,
    write_terminal_recommendation,
)
from .layout import MultiAgentPaths, create_multiagent_layout
from .leaderboard import best_completed_attempt, read_budget_state, render_leaderboard
from .reconcile import detect_mismatches
from .runtime import AgentHandle, ClaudeAgentRuntime, extract_session_id
from .state import prune_stale_active_attempts, set_attempt_admissions


def run_multiagent_loop(
    contract: WorkspaceContract,
    driver: ClaudeDriver,
    config: MultiAgentConfig | None = None,
    *,
    runtime: ClaudeAgentRuntime | None = None,
    sleep_fn=time.sleep,
    clock_fn=time.monotonic,
) -> int:
    """Run the RSIBench-Data multi-agent lifecycle."""
    config = config or load_multiagent_config(default_model=contract.model)
    if not config.enabled:
        raise ValueError("multi-agent loop called with disabled config")

    paths = create_multiagent_layout(contract.work_dir, config)
    initial_active = prune_stale_active_attempts(
        contract.budget_state_path,
        paths.budget_lock,
    )
    if not _should_finalize(contract, config, has_active=bool(initial_active)):
        set_attempt_admissions(
            contract.budget_state_path,
            paths.budget_lock,
            closed=False,
        )
    runtime = runtime or ClaudeAgentRuntime(
        contract,
        config,
        driver,
        paths.public / "logs" / "agents",
    )
    handles = _start_agents(runtime, paths, config)
    finalizer = BestAttemptFinalizer(contract=contract, lock_path=paths.budget_lock)
    error_states = _read_error_states(paths)
    draining = False
    forced_stop_sent = False

    try:
        iteration = 0
        while True:
            iteration += 1
            active = prune_stale_active_attempts(contract.budget_state_path, paths.budget_lock)
            render_leaderboard(
                contract.budget_state_path,
                paths.public / "leaderboard.json",
                paths.budget_lock,
            )
            terminal = read_terminal_recommendations(
                paths.public,
                config.agent_ids,
                contract.budget_state_path,
                paths.budget_lock,
            )
            error_states = _observe_agent_errors(
                paths,
                handles,
                config,
                error_states,
                terminal=terminal,
                budget_state_path=contract.budget_state_path,
                now=clock_fn(),
            )
            terminal = read_terminal_recommendations(
                paths.public,
                config.agent_ids,
                contract.budget_state_path,
                paths.budget_lock,
            )
            _stop_terminal_agents(handles, set(terminal))
            phase = "draining" if draining else "running"
            _write_run_status(
                paths,
                config,
                handles,
                phase=phase,
                active=active,
                terminal=terminal,
            )
            if contract.final_selection_exists():
                _write_run_status(paths, config, handles, phase="final_selection_exists")
                return 0
            if draining or _should_finalize(
                contract,
                config,
                has_active=bool(active),
                all_agents_terminal=len(terminal) == config.num_agents,
            ):
                if not draining:
                    draining = True
                    set_attempt_admissions(
                        contract.budget_state_path,
                        paths.budget_lock,
                        closed=True,
                    )
                active = prune_stale_active_attempts(
                    contract.budget_state_path,
                    paths.budget_lock,
                )
                active_owners = _active_owners(active)
                _stop_idle_agents(handles, active_owners)
                if active:
                    _write_run_status(paths, config, handles, phase="draining", active=active)
                    if contract.remaining_wall_time() <= contract.min_remaining_sec:
                        _terminate_active_attempts(
                            active,
                            signal.SIGKILL if forced_stop_sent else signal.SIGTERM,
                        )
                        forced_stop_sent = True
                    sleep_fn(min(config.monitor_interval_sec, 5))
                    continue
                result = _finalize(contract, paths, finalizer, driver.env)
                _write_finalize_result(paths, result)
                return 0 if result.submitted or result.reason == "already_submitted" else 1
            if config.max_manager_iterations and iteration >= config.max_manager_iterations:
                _write_run_status(paths, config, handles, phase="max_manager_iterations")
                return 0
            if config.restart_agents:
                _restart_dead_agents(
                    runtime,
                    paths,
                    handles,
                    active_owners=_active_owners(active),
                    terminal_ids=set(terminal),
                )
            sleep_fn(config.monitor_interval_sec)
    finally:
        for handle in handles:
            handle.stop()
            _persist_handle_session(paths, handle)


def _start_agents(runtime: ClaudeAgentRuntime, paths: MultiAgentPaths, config: MultiAgentConfig) -> list[AgentHandle]:
    handles: list[AgentHandle] = []
    for agent_id in config.agent_ids:
        try:
            handles.append(
                runtime.start(
                    agent_id,
                    paths.agent_workspace(agent_id),
                    resume_session_id=_read_persisted_session(paths, agent_id),
                )
            )
        except Exception as exc:
            handles.append(_failed_agent_handle(paths, agent_id, exc))
    return handles


def _restart_dead_agents(
    runtime: ClaudeAgentRuntime,
    paths: MultiAgentPaths,
    handles: list[AgentHandle],
    *,
    active_owners: set[str] | None = None,
    terminal_ids: set[str] | None = None,
) -> None:
    active_owners = active_owners or set()
    terminal_ids = terminal_ids or set()
    for idx, handle in enumerate(list(handles)):
        if handle.alive:
            continue
        if handle.agent_id in terminal_ids:
            continue
        if handle.agent_id in active_owners:
            continue
        extracted_session_id = extract_session_id(handle.log_path)
        if extracted_session_id:
            session_id = extracted_session_id
            _write_persisted_session(paths, handle.agent_id, session_id)
        elif handle.session_id:
            session_id = None
            _clear_persisted_session(paths, handle.agent_id)
        else:
            session_id = None
        try:
            handles[idx] = runtime.start(
                handle.agent_id,
                paths.agent_workspace(handle.agent_id),
                resume_session_id=session_id,
            )
        except Exception as exc:
            handles[idx] = _failed_agent_handle(paths, handle.agent_id, exc)


def _failed_agent_handle(paths: MultiAgentPaths, agent_id: str, error: Exception) -> AgentHandle:
    logs_dir = paths.public / "logs" / "agents"
    logs_dir.mkdir(parents=True, exist_ok=True)
    generation = len(list(logs_dir.glob(f"{agent_id}.start-failure.*.err")))
    log_path = logs_dir / f"{agent_id}.start-failure.{generation}.jsonl"
    err_path = logs_dir / f"{agent_id}.start-failure.{generation}.err"
    log_path.write_text("", encoding="utf-8")
    err_path.write_text(f"agent startup failed: {error}\n", encoding="utf-8")
    return AgentHandle(agent_id, None, paths.agent_workspace(agent_id), log_path, err_path)


def _should_finalize(
    contract: WorkspaceContract,
    config: MultiAgentConfig,
    *,
    has_active: bool = False,
    all_agents_terminal: bool = False,
) -> bool:
    if contract.final_selection_exists():
        return True
    if all_agents_terminal:
        return True
    if contract.remaining_wall_time() <= config.final_buffer_sec:
        return True
    if contract.remaining_cost() <= 0 and not has_active:
        return True
    return False


def _finalize(
    contract: WorkspaceContract,
    paths: MultiAgentPaths,
    finalizer: BestAttemptFinalizer,
    env: dict,
) -> FinalizeResult:
    report = detect_mismatches(
        paths.public / "attempts",
        contract.budget_state_path,
        paths.budget_lock,
    )
    (paths.private / "reconcile_report.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_leaderboard(contract.budget_state_path, paths.public / "leaderboard.json", paths.budget_lock)
    return finalizer.submit_best(env=env)


def _write_run_status(
    paths: MultiAgentPaths,
    config: MultiAgentConfig,
    handles: list[AgentHandle],
    *,
    phase: str,
    active: list[dict] | None = None,
    terminal: dict[str, TerminalRecommendation] | None = None,
) -> None:
    payload = {
        "phase": phase,
        "agents": [
            {
                "agent_id": h.agent_id,
                "alive": h.alive,
                "workspace": str(h.workspace),
                "log_path": str(h.log_path),
                "err_path": str(h.err_path),
                "session_id": h.session_id,
                "terminal": h.agent_id in (terminal or {}),
                "terminal_reason": (
                    terminal[h.agent_id].reason if terminal and h.agent_id in terminal else None
                ),
                "terminal_source": (
                    terminal[h.agent_id].source if terminal and h.agent_id in terminal else None
                ),
            }
            for h in handles
        ],
        "configured_agents": list(config.agent_ids),
        "active_attempts": active or [],
        "terminal_agents": sorted(terminal or {}),
    }
    (paths.public / "run_status.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_finalize_result(paths: MultiAgentPaths, result: FinalizeResult) -> None:
    payload = {
        "submitted": result.submitted,
        "attempt_id": result.attempt_id,
        "reason": result.reason,
        "return_code": result.return_code,
    }
    (paths.public / "final_decision.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _active_owners(active: list[dict]) -> set[str]:
    return {
        owner
        for item in active
        if isinstance(item, dict)
        for owner in [item.get("owner_id")]
        if isinstance(owner, str) and owner
    }


def _stop_idle_agents(handles: list[AgentHandle], active_owners: set[str]) -> None:
    for handle in handles:
        if handle.agent_id not in active_owners:
            handle.stop()


def _stop_terminal_agents(handles: list[AgentHandle], terminal_ids: set[str]) -> None:
    for handle in handles:
        if handle.agent_id in terminal_ids:
            handle.stop()


def _observe_agent_errors(
    paths: MultiAgentPaths,
    handles: list[AgentHandle],
    config: MultiAgentConfig,
    error_states: dict[str, dict[str, Any]],
    *,
    terminal: dict[str, TerminalRecommendation],
    budget_state_path: Path,
    now: float,
) -> dict[str, dict[str, Any]]:
    changed = False
    for handle in handles:
        agent_id = handle.agent_id
        if agent_id in terminal:
            if error_states.pop(agent_id, None) is not None:
                changed = True
            continue
        evidence = _agent_error_evidence(handle)
        state = error_states.get(agent_id)
        if evidence:
            if state is None:
                state = {"error_since": now}
                error_states[agent_id] = state
                changed = True
            state["last_error"] = evidence
            state["last_error_path"] = str(handle.err_path)
            state.pop("recovery_since", None)
            changed = True
            if now - float(state["error_since"]) >= config.agent_error_grace_sec:
                _write_manager_error_recommendation(paths, budget_state_path, agent_id, state)
                error_states.pop(agent_id, None)
                changed = True
            continue
        if state is None or not handle.alive:
            continue
        recovery_since = state.get("recovery_since")
        if recovery_since is None:
            state["recovery_since"] = now
            changed = True
        elif now - float(recovery_since) >= config.monitor_interval_sec:
            error_states.pop(agent_id, None)
            changed = True
    if changed:
        _write_error_states(paths, error_states)
    return error_states


def _agent_error_evidence(handle: AgentHandle) -> str | None:
    try:
        stderr = handle.err_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        stderr = ""
    if stderr:
        return stderr[-2000:]
    process = handle.process
    if process is not None and process.poll() is not None and process.returncode not in (0, None):
        return f"agent process exited with return code {process.returncode}"
    return None


def _write_manager_error_recommendation(
    paths: MultiAgentPaths,
    budget_state_path: Path,
    agent_id: str,
    error_state: dict[str, Any],
) -> None:
    state = read_budget_state(budget_state_path)
    best = best_completed_attempt(state)
    recommendation = TerminalRecommendation(
        agent_id=agent_id,
        decision="submit",
        source="manager",
        reason="Agent error did not recover within the configured grace period.",
        reason_type="agent_error_timeout",
        attempt_id=best.attempt_id if best else None,
        score=best.harbor_score if best else None,
        evaluation_scope="full" if best else None,
        error_since=float(error_state["error_since"]),
        last_error=str(error_state.get("last_error", "agent error")),
    )
    write_terminal_recommendation(paths.public, recommendation)


def _error_state_path(paths: MultiAgentPaths) -> Path:
    return paths.private / "process_state" / "agent_errors.json"


def _read_error_states(paths: MultiAgentPaths) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_error_state_path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        agent_id: dict(value)
        for agent_id, value in data.items()
        if isinstance(agent_id, str) and isinstance(value, dict)
    }


def _write_error_states(paths: MultiAgentPaths, states: dict[str, dict[str, Any]]) -> None:
    path = _error_state_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(raw_tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(states, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _terminate_active_attempts(active: list[dict], sig: signal.Signals) -> None:
    for item in active:
        pid = item.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue


def _session_path(paths: MultiAgentPaths, agent_id: str) -> Path:
    return paths.private / "sessions" / f"{agent_id}.json"


def _read_persisted_session(paths: MultiAgentPaths, agent_id: str) -> str | None:
    path = _session_path(paths, agent_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _session_from_latest_log(paths, agent_id)
    session_id = data.get("session_id") if isinstance(data, dict) else None
    return session_id if isinstance(session_id, str) and session_id else None


def _session_from_latest_log(paths: MultiAgentPaths, agent_id: str) -> str | None:
    logs = paths.public / "logs" / "agents"
    candidates = sorted(
        logs.glob(f"{agent_id}.*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        session_id = extract_session_id(path)
        if session_id:
            _write_persisted_session(paths, agent_id, session_id)
            return session_id
    return None


def _write_persisted_session(
    paths: MultiAgentPaths,
    agent_id: str,
    session_id: str,
) -> None:
    path = _session_path(paths, agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"agent_id": agent_id, "session_id": session_id}, indent=2) + "\n",
        encoding="utf-8",
    )


def _clear_persisted_session(paths: MultiAgentPaths, agent_id: str) -> None:
    try:
        _session_path(paths, agent_id).unlink()
    except FileNotFoundError:
        pass


def _persist_handle_session(paths: MultiAgentPaths, handle: AgentHandle) -> None:
    session_id = extract_session_id(handle.log_path) or handle.session_id
    if session_id:
        _write_persisted_session(paths, handle.agent_id, session_id)
