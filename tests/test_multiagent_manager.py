"""Tests for the multi-agent manager with fake runtime handles."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from godel_forest.contract import REQUIRED_WORKSPACE_SCRIPTS, load_contract
from godel_forest.multiagent.config import MultiAgentConfig
from godel_forest.multiagent.final_recommendations import read_terminal_recommendations
from godel_forest.multiagent.layout import MultiAgentPaths, create_multiagent_layout
from godel_forest.multiagent.manager import (
    _observe_agent_errors,
    _restart_dead_agents,
    _start_agents,
    run_multiagent_loop,
)
from godel_forest.multiagent.runtime import AgentHandle


def _contract(root: Path, *, wall: int = 100, cost: float = 10.0, iterations: list[dict] | None = None):
    session = root / "session"
    work = session / "agent_workspace"
    state_dir = session / "runner_state"
    work.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    for name in REQUIRED_WORKSPACE_SCRIPTS:
        (work / name).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    prompt = session / "prompt.md"
    prompt.write_text("go\n", encoding="utf-8")
    (state_dir / "budget_state.json").write_text(
        json.dumps(
            {
                "started_at": 0,
                "budget": {"wall_time_budget_sec": wall},
                "remaining_tinker_cost_usd": cost,
                "iterations": iterations or [],
            }
        ),
        encoding="utf-8",
    )
    return load_contract(
        {
            "RSIBENCH_DATA_WORK_DIR": str(work),
            "RSIBENCH_DATA_REPO_ROOT": str(root),
            "RSIBENCH_DATA_PROMPT_PATH": str(prompt),
            "ANTHROPIC_MODEL": "m",
        }
    )


class _Driver:
    env = {}


class _Runtime:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.start_calls: list[tuple[str, str | None]] = []

    def start(self, agent_id, workspace, *, resume_session_id=None):
        self.started.append(agent_id)
        self.start_calls.append((agent_id, resume_session_id))
        log = workspace / f"{agent_id}.jsonl"
        err = workspace / f"{agent_id}.err"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return AgentHandle(agent_id, None, workspace, log, err, resume_session_id)


class _ErroredProcess:
    returncode = 1

    def poll(self):
        return self.returncode


class TestMultiAgentManager(unittest.TestCase):
    def test_restart_dead_agent_resumes_session_from_completed_log(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = MultiAgentPaths(
                work_dir=root,
                root=root / ".godel_forest",
                public=root / ".godel_forest" / "public",
                private=root / ".godel_forest" / "private",
                agents_dir=root / "agents",
            )
            workspace = paths.agent_workspace("agent001")
            workspace.mkdir(parents=True)
            log = workspace / "completed.jsonl"
            log.write_text(
                json.dumps({"type": "result", "session_id": "session-agent001"})
                + "\n",
                encoding="utf-8",
            )
            err = workspace / "completed.err"
            err.write_text("", encoding="utf-8")
            handles = [AgentHandle("agent001", None, workspace, log, err)]
            runtime = _Runtime()

            _restart_dead_agents(runtime, paths, handles)

            self.assertEqual(runtime.start_calls, [("agent001", "session-agent001")])
            self.assertEqual(handles[0].session_id, "session-agent001")
            session_state = json.loads(
                (paths.private / "sessions" / "agent001.json").read_text(encoding="utf-8")
            )
            self.assertEqual(session_state["session_id"], "session-agent001")

    def test_restart_dead_agent_starts_fresh_when_log_has_no_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = MultiAgentPaths(
                work_dir=root,
                root=root / ".godel_forest",
                public=root / ".godel_forest" / "public",
                private=root / ".godel_forest" / "private",
                agents_dir=root / "agents",
            )
            workspace = paths.agent_workspace("agent001")
            workspace.mkdir(parents=True)
            log = workspace / "completed.jsonl"
            log.write_text("not json\n", encoding="utf-8")
            err = workspace / "completed.err"
            err.write_text("", encoding="utf-8")
            handles = [AgentHandle("agent001", None, workspace, log, err)]
            runtime = _Runtime()

            _restart_dead_agents(runtime, paths, handles)

            self.assertEqual(runtime.start_calls, [("agent001", None)])
            self.assertIsNone(handles[0].session_id)

    def test_restart_waits_while_owner_has_active_attempt(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = MultiAgentPaths(
                work_dir=root,
                root=root / ".godel_forest",
                public=root / ".godel_forest" / "public",
                private=root / ".godel_forest" / "private",
                agents_dir=root / "agents",
            )
            workspace = paths.agent_workspace("agent001")
            workspace.mkdir(parents=True)
            log = workspace / "completed.jsonl"
            log.write_text("", encoding="utf-8")
            err = workspace / "completed.err"
            err.write_text("", encoding="utf-8")
            handles = [AgentHandle("agent001", None, workspace, log, err)]
            runtime = _Runtime()

            _restart_dead_agents(
                runtime,
                paths,
                handles,
                active_owners={"agent001"},
            )

            self.assertEqual(runtime.start_calls, [])

    def test_start_agents_uses_persisted_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = MultiAgentPaths(
                work_dir=root,
                root=root / ".godel_forest",
                public=root / ".godel_forest" / "public",
                private=root / ".godel_forest" / "private",
                agents_dir=root / "agents",
            )
            session_path = paths.private / "sessions" / "agent001.json"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps({"session_id": "persisted-session"}),
                encoding="utf-8",
            )
            runtime = _Runtime()
            cfg = MultiAgentConfig(enabled=True, agent_ids=("agent001",), model="m")

            _start_agents(runtime, paths, cfg)

            self.assertEqual(runtime.start_calls, [("agent001", "persisted-session")])

    def test_manager_starts_agents_and_noops_without_completed_attempt(self) -> None:
        with TemporaryDirectory() as tmp:
            contract = _contract(Path(tmp), wall=100, cost=0.0)
            cfg = MultiAgentConfig(
                enabled=True,
                agent_ids=("agent001", "agent002"),
                model="m",
                final_buffer_sec=3600,
                max_manager_iterations=0,
            )
            runtime = _Runtime()
            rc = run_multiagent_loop(contract, _Driver(), cfg, runtime=runtime, sleep_fn=lambda _: None)
            self.assertEqual(rc, 1)
            self.assertEqual(runtime.started, ["agent001", "agent002"])
            self.assertTrue((contract.work_dir / ".godel_forest" / "public" / "final_decision.json").is_file())

    def test_manager_submits_best_when_budget_low(self) -> None:
        with TemporaryDirectory() as tmp:
            contract = _contract(
                Path(tmp),
                wall=100,
                cost=1.0,
                iterations=[{"attempt_id": "agent001_001", "status": "completed", "harbor_score": 0.7}],
            )
            # Make final_submit.sh create the completion marker so the contract sees it.
            contract.script("final_submit.sh").write_text(
                "#!/usr/bin/env bash\npython3 - <<'PY'\nfrom pathlib import Path\nPath('final_selection.json').write_text('{}')\nPY\n",
                encoding="utf-8",
            )
            cfg = MultiAgentConfig(enabled=True, agent_ids=("agent001",), model="m", final_buffer_sec=3600)
            rc = run_multiagent_loop(contract, _Driver(), cfg, runtime=_Runtime(), sleep_fn=lambda _: None)
            self.assertEqual(rc, 0)
            self.assertTrue(contract.final_selection_exists())

    def test_error_timeout_writes_manager_terminal_recommendation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = MultiAgentPaths(
                work_dir=root,
                root=root / ".godel_forest",
                public=root / ".godel_forest" / "public",
                private=root / ".godel_forest" / "private",
                agents_dir=root / "agents",
            )
            paths.public.mkdir(parents=True)
            paths.private.mkdir(parents=True)
            err = root / "agent.err"
            err.write_text("model access error", encoding="utf-8")
            log = root / "agent.jsonl"
            log.write_text("", encoding="utf-8")
            handle = AgentHandle("agent001", _ErroredProcess(), root, log, err)
            cfg = MultiAgentConfig(
                enabled=True,
                agent_ids=("agent001",),
                model="m",
                agent_error_grace_sec=300,
            )
            state = {}
            budget = root / "budget_state.json"
            budget.write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "attempt_id": "agent001_001",
                                "status": "completed",
                                "harbor_score": 0.5,
                                "evaluation_scope": "full",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            terminal = {}

            state = _observe_agent_errors(
                paths,
                [handle],
                cfg,
                state,
                terminal=terminal,
                budget_state_path=budget,
                now=0.0,
            )
            self.assertEqual(
                read_terminal_recommendations(
                    paths.public, cfg.agent_ids, budget, paths.budget_lock
                ),
                {},
            )

            _observe_agent_errors(
                paths,
                [handle],
                cfg,
                state,
                terminal=terminal,
                budget_state_path=budget,
                now=300.0,
            )
            recommendations = read_terminal_recommendations(
                paths.public, cfg.agent_ids, budget, paths.budget_lock
            )
            self.assertEqual(recommendations["agent001"].attempt_id, "agent001_001")
            self.assertEqual(recommendations["agent001"].source, "manager")

    def test_all_terminal_agents_submit_current_best_immediately(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _contract(
                root,
                wall=10000,
                cost=10.0,
                iterations=[
                    {"attempt_id": "agent001_001", "status": "completed", "harbor_score": 0.2},
                    {"attempt_id": "agent002_001", "status": "completed", "harbor_score": 0.7},
                ],
            )
            contract.script("final_submit.sh").write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s' \"$1\" > final_selection.json\n",
                encoding="utf-8",
            )
            cfg = MultiAgentConfig(
                enabled=True,
                agent_ids=("agent001", "agent002"),
                model="m",
                final_buffer_sec=0,
            )
            paths = create_multiagent_layout(contract.work_dir, cfg)
            for agent_id in cfg.agent_ids:
                (paths.public / "final_recommendations" / f"{agent_id}.json").write_text(
                    json.dumps(
                        {
                            "agent_id": agent_id,
                            "decision": "submit",
                            "source": "agent",
                            "reason": "done",
                            "reason_type": "agent_decision",
                            "attempt_id": "agent001_001",
                            "score": 0.2,
                            "evaluation_scope": "full",
                        }
                    ),
                    encoding="utf-8",
                )

            rc = run_multiagent_loop(
                contract,
                _Driver(),
                cfg,
                runtime=_Runtime(),
                sleep_fn=lambda _: None,
            )

            self.assertEqual(rc, 0)
            self.assertEqual(contract.final_selection_path.read_text(encoding="utf-8"), "agent002_001")


if __name__ == "__main__":
    unittest.main()
