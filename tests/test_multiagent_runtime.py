"""Tests for multi-agent runtime helpers."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from godel_forest.contract import WorkspaceContract
from godel_forest.multiagent.config import MultiAgentConfig
from godel_forest.multiagent.runtime import ClaudeAgentRuntime, extract_session_id


class TestRuntimeHelpers(unittest.TestCase):
    def test_extract_session_id_prefers_result_line(self) -> None:
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "agent.jsonl"
            log.write_text(
                json.dumps({"session_id": "early"})
                + "\n"
                + json.dumps({"type": "result", "session_id": "result"})
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(extract_session_id(log), "result")

    def test_extract_session_id_falls_back_to_any_line(self) -> None:
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "agent.jsonl"
            log.write_text("not json\n" + json.dumps({"session_id": "sid"}) + "\n", encoding="utf-8")
            self.assertEqual(extract_session_id(log), "sid")

    def test_runtime_injects_attempt_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "agent_workspace"
            workspace = work / "agents" / "agent001"
            logs = work / ".godel_forest" / "public" / "logs" / "agents"
            workspace.mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("base prompt", encoding="utf-8")
            contract = WorkspaceContract(
                work_dir=work,
                repo_root=root,
                prompt_path=prompt,
                budget_state_path=root / "runner_state" / "budget_state.json",
                model="m",
                min_remaining_sec=10,
            )

            class Driver:
                env = {}
                _auth_args = []
                _allowed_tools = "Bash"
                _permission_args = []

            class Process:
                stdout = None
                stderr = None

                def poll(self):
                    return None

            runtime = ClaudeAgentRuntime(
                contract,
                MultiAgentConfig(enabled=True, agent_ids=("agent001",), model="m"),
                Driver(),
                logs,
            )
            with patch("godel_forest.multiagent.runtime.subprocess.Popen", return_value=Process()) as popen:
                runtime.start("agent001", workspace)

            self.assertEqual(popen.call_args.kwargs["env"]["DGB_ATTEMPT_OWNER"], "agent001")


if __name__ == "__main__":
    unittest.main()
