"""Claude Code subprocess runtime for multi-agent workers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from godel_forest.claude_driver import ClaudeDriver, CodexDriver
from godel_forest.contract import WorkspaceContract

from .config import MultiAgentConfig
from .prompts import render_initial_prompt, render_resume_prompt


@dataclass
class AgentHandle:
    agent_id: str
    process: subprocess.Popen | None
    workspace: Path
    log_path: Path
    err_path: Path
    session_id: str | None = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 10.0) -> None:
        if self.process is None:
            return
        if self.alive:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    self.process.kill()
                self.process.wait(timeout=5)
        self.session_id = extract_session_id(self.log_path) or self.session_id
        if self.process.stdout:
            try:
                self.process.stdout.close()
            except OSError:
                pass
        if self.process.stderr:
            try:
                self.process.stderr.close()
            except OSError:
                pass


class ClaudeAgentRuntime:
    """Start and stop Claude Code workers in per-agent workspaces."""

    def __init__(
        self,
        contract: WorkspaceContract,
        config: MultiAgentConfig,
        driver: ClaudeDriver,
        logs_dir: Path,
    ) -> None:
        self.contract = contract
        self.config = config
        self.driver = driver
        self.logs_dir = Path(logs_dir)

    def start(
        self,
        agent_id: str,
        workspace: Path,
        *,
        resume_session_id: str | None = None,
    ) -> AgentHandle:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        generation = len(list(self.logs_dir.glob(f"{agent_id}.*.jsonl")))
        log_path = self.logs_dir / f"{agent_id}.{generation}.jsonl"
        err_path = self.logs_dir / f"{agent_id}.{generation}.err"
        prompt = (
            render_resume_prompt(
                agent_id,
                work_dir=self.contract.work_dir,
                worker_workspace=workspace,
            )
            if resume_session_id
            else render_initial_prompt(
                agent_id,
                self.contract.work_dir,
                self.contract.prompt_path.read_text(encoding="utf-8"),
                worker_workspace=workspace,
            )
        )
        if isinstance(self.driver, CodexDriver):
            self.driver._ensure_proxy()
            cmd = self.driver._command(
                prompt,
                log_path.with_suffix(".last.txt"),
                resume=resume_session_id is not None,
                work_dir=workspace,
                model=self.config.model or self.contract.model,
            )
        else:
            cmd = self._command(prompt, resume_session_id=resume_session_id)
        env = dict(self.driver.env)
        env["ANTHROPIC_MODEL"] = self.config.model or self.contract.model
        env["DGB_ATTEMPT_OWNER"] = agent_id
        if isinstance(self.driver, CodexDriver):
            codex_home = workspace / ".codex_home"
            codex_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(codex_home)
        with log_path.open("w", encoding="utf-8") as log, err_path.open("w", encoding="utf-8") as err:
            stderr_target = subprocess.STDOUT if isinstance(self.driver, CodexDriver) else err
            process = subprocess.Popen(
                cmd,
                cwd=str(workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=stderr_target,
                text=True,
                start_new_session=True,
            )
        return AgentHandle(agent_id, process, workspace, log_path, err_path, resume_session_id)

    def _command(self, prompt: str, *, resume_session_id: str | None) -> list[str]:
        model = self.config.model or self.contract.model
        cmd = ["claude", *self.driver._auth_args, "--print", prompt]
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        flags = [
            "--model",
            model,
            "--verbose",
            "--output-format",
            "stream-json",
            "--thinking-display",
            "summarized",
            "--add-dir",
            str(self.contract.work_dir),
            "--allowedTools",
            self.driver._allowed_tools,
            *self.driver._permission_args,
        ]
        extra = self.driver.env.get("CLAUDE_CODE_ARGS", "")
        if extra:
            flags.extend(extra.split())
        return [*cmd, *flags]


def extract_session_id(log_path: Path) -> str | None:
    """Extract a Claude Code stream-json session id from a log."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            data: Any = json.loads(line)
        except ValueError:
            continue
        if data.get("type") == "result" and data.get("session_id"):
            return str(data["session_id"])
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        sid = data.get("session_id")
        if sid:
            return str(sid)
    return None
