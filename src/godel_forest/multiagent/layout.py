"""Create shared public state and isolated per-agent workspaces."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from godel_forest.evidence_workflow import (
    ASSET_PACKAGE,
    FLOW_FILENAME,
    WORKFLOW_SKILL_PATHS,
    install_evidence_workflow,
)

from .config import MultiAgentConfig
from .locks import budget_lock_path
from .prompts import render_agent_workspace_doc

PUBLIC_DIRS = (
    "notes",
    "skills",
    "messages",
    "candidates",
    "probes",
    "attempts",
    "logs",
    "final_recommendations",
)
PRIVATE_DIRS = ("locks", "sessions", "process_state")
SHARED_LINKS = (
    "notes",
    "skills",
    "messages",
    "candidates",
    "probes",
    "attempts",
    "final_recommendations",
)


@dataclass(frozen=True)
class MultiAgentPaths:
    """Filesystem paths for a multi-agent run."""

    work_dir: Path
    root: Path
    public: Path
    private: Path
    agents_dir: Path

    def agent_workspace(self, agent_id: str) -> Path:
        return self.agents_dir / agent_id

    def public_dir(self, name: str) -> Path:
        return self.public / name

    @property
    def budget_lock(self) -> Path:
        return budget_lock_path(self.work_dir)


def create_multiagent_layout(work_dir: Path, config: MultiAgentConfig) -> MultiAgentPaths:
    """Create the multi-agent workspace layout and return its path bundle."""
    work_dir = Path(work_dir)
    root = work_dir / ".godel_forest"
    public = root / "public"
    private = root / "private"
    agents_dir = work_dir / "agents"
    paths = MultiAgentPaths(
        work_dir=work_dir,
        root=root,
        public=public,
        private=private,
        agents_dir=agents_dir,
    )

    for directory in (public, private, agents_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_DIRS:
        (public / name).mkdir(parents=True, exist_ok=True)
    for name in PRIVATE_DIRS:
        (private / name).mkdir(parents=True, exist_ok=True)
    (public / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (public / "messages" / "broadcast").mkdir(parents=True, exist_ok=True)
    _remove_obsolete_gate_artifacts(public)

    _write_json_if_absent(public / "leaderboard.json", {"baseline": None, "attempts": [], "best": None})
    _write_json_if_absent(public / "run_status.json", {"phase": "starting", "agents": list(config.agent_ids)})
    _write_workflow_skills(public)

    for agent_id in config.agent_ids:
        _create_agent_workspace(paths, agent_id)

    return paths


def _create_agent_workspace(paths: MultiAgentPaths, agent_id: str) -> None:
    workspace = paths.agent_workspace(agent_id)
    for rel in ("work", "candidate_work", "tmp"):
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    for name in SHARED_LINKS:
        target = paths.public / name
        link = claude_dir / name
        _ensure_symlink(link, target)

    install_evidence_workflow(paths.work_dir, state_dir=workspace)
    (workspace / "GODEL_FOREST_AGENT.md").write_text(
        render_agent_workspace_doc(
            agent_id,
            work_dir=paths.work_dir,
            worker_workspace=workspace,
        ),
        encoding="utf-8",
    )
    _write_claude_settings(paths, agent_id)


def _ensure_symlink(link: Path, target: Path) -> None:
    if link.is_symlink():
        current = os.readlink(link)
        desired = os.path.relpath(target.resolve(), link.parent.resolve())
        if current == desired or Path(current) == target.resolve():
            return
        link.unlink()
    elif link.exists():
        return
    rel = os.path.relpath(target.resolve(), link.parent.resolve())
    link.symlink_to(rel)


def _write_claude_settings(paths: MultiAgentPaths, agent_id: str) -> None:
    """Write final-submit/private guardrails for one Claude worker."""
    workspace = paths.agent_workspace(agent_id).resolve()
    work_dir = paths.work_dir.resolve()
    public = paths.public.resolve()
    private = paths.private.resolve()
    attempt_prefix = (paths.work_dir / "attempts" / f"{agent_id}_*").resolve()

    instruction_files = [
        "RUN_CONTRACT.md",
        "AGENT_RULES.md",
        "DATA_GENERATION.md",
        "MINI_SWE_DATA_FORMAT.md",
        "EVAL_ATTEMPT_LOOP.md",
        "timer.sh",
        "budget_status.sh",
        "run_attempt.sh",
    ]
    allow: list[str] = [
        "Bash",
        _rule("Read", workspace, recursive=True),
        _rule("Edit", workspace, recursive=True),
        _rule("Write", workspace, recursive=True),
        _rule("Read", public, recursive=True),
        _rule("Edit", public, recursive=True),
        _rule("Write", public, recursive=True),
        _rule("Read", work_dir / "attempts", recursive=True),
        _rule("Edit", attempt_prefix, recursive=True),
        _rule("Write", attempt_prefix, recursive=True),
        "WebSearch",
        "WebFetch",
    ]
    for filename in instruction_files:
        allow.append(_rule("Read", work_dir / filename))
    allow.append(_rule("Read", work_dir / FLOW_FILENAME))

    deny: list[str] = [
        _rule("Read", private, recursive=True),
        _rule("Edit", private, recursive=True),
        _rule("Write", private, recursive=True),
        "Bash(*final_submit.sh*)",
        "Bash(*validate_final_selection.py*)",
        "Bash(*final_selection.json*)",
        "Bash(*nohup*)",
        "Bash(*disown*)",
        "Bash(*setsid*)",
        f"Bash(*{private}*)",
        _rule("Edit", work_dir / "final_selection.json"),
        _rule("Write", work_dir / "final_selection.json"),
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
    ]

    settings = {"permissions": {"allow": allow, "deny": deny}}
    path = paths.agent_workspace(agent_id) / ".claude" / "settings.local.json"
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _rule(tool: str, path: Path, *, recursive: bool = False) -> str:
    raw = str(path)
    if recursive:
        raw = raw.rstrip("/") + "/**"
    return f"{tool}({raw})"


def _write_json_if_absent(path: Path, payload: dict) -> None:
    if not path.exists():
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text_if_absent(path: Path, content: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _remove_obsolete_gate_artifacts(public: Path) -> None:
    for path in (
        public / "skills" / "quick-signal-eval",
        public / "logs" / "gated_attempts",
        public / "bin" / "gated_attempt.sh",
        public / "bin" / "staged_eval.py",
    ):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    bin_dir = public / "bin"
    if bin_dir.is_dir() and not any(bin_dir.iterdir()):
        bin_dir.rmdir()



def _write_workflow_skills(public: Path) -> None:
    """Expose workflow skills through the shared public skills directory."""
    for skill_path in WORKFLOW_SKILL_PATHS:
        content = files(ASSET_PACKAGE).joinpath(*skill_path.parts).read_text(
            encoding="utf-8"
        )
        rel = Path(*skill_path.parts[1:])
        path = public / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
