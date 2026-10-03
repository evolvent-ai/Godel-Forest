"""Claude Code and Codex CLI drivers.

A faithful port of how ``runner/run_session.sh`` invokes the ``claude`` CLI for
the ``claude-code`` harness: same flags, same auth handling, same per-call
wall-time timeout. We reimplement it here (rather than shelling back into the
harness) so the external agent owns its own loop while still reproducing the
exact current agent behavior for v0 link validation.

Reference: run_session.sh lines ~568-634 (flag assembly, configure_claude_code_env,
run_claude_print/run_claude_continue).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .contract import WorkspaceContract


@dataclass
class ClaudeDriver:
    """Builds and runs ``claude`` CLI invocations under the harness contract."""

    contract: WorkspaceContract
    env: dict = field(default_factory=lambda: dict(os.environ))

    @property
    def cli_name(self) -> str:
        return "Claude Code"

    def __post_init__(self) -> None:
        self._unset_empty_overrides()
        # Match current configure_claude_code_env. Auth tokens are passed
        # directly; API keys use Claude's bare project/local setting sources.
        self._auth_args: list[str] = []
        base_url = self.env.get("ANTHROPIC_BASE_URL")
        api_key = self.env.get("ANTHROPIC_API_KEY")
        auth_token = self.env.get("ANTHROPIC_AUTH_TOKEN")
        if base_url and auth_token:
            self.env.pop("ANTHROPIC_API_KEY", None)
        elif base_url and api_key:
            # contract.load_contract already enforced both-or-neither.
            self.env.pop("ANTHROPIC_AUTH_TOKEN", None)
            self._auth_args = ["--bare", "--setting-sources", "project,local"]
        self.env["ANTHROPIC_MODEL"] = self.contract.model

        self._allowed_tools = self.env.get(
            "CLAUDE_ALLOWED_TOOLS", "Bash,Read,Edit,Write,Glob,Grep,WebSearch,WebFetch"
        )
        self._permission_args = self._resolve_permission_args()

    def _unset_empty_overrides(self) -> None:
        for key in (
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_FABLE_MODEL",
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "ENABLE_TOOL_SEARCH",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_EFFORT_LEVEL",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        ):
            if key in self.env and not self.env[key]:
                self.env.pop(key)

    def _resolve_permission_args(self) -> list[str]:
        """Mirror the CLAUDE_PERMISSION_ARGS block in run_session.sh."""
        mode = self.env.get("CLAUDE_PERMISSION_MODE")
        if mode:
            return ["--permission-mode", mode]
        if self.env.get("CLAUDE_SKIP_PERMISSIONS", "1") == "1":
            if os.geteuid() == 0:
                return ["--permission-mode", "acceptEdits"]
            return ["--dangerously-skip-permissions"]
        return []

    def _base_command(self) -> list[str]:
        cmd = ["claude", *self._auth_args]
        return cmd

    def _common_flags(self) -> list[str]:
        extra = self.env.get("CLAUDE_CODE_ARGS", "")
        flags = [
            "--model",
            self.contract.model,
            "--verbose",
            "--output-format",
            "stream-json",
            "--thinking-display",
            "summarized",
            "--allowedTools",
            self._allowed_tools,
            *self._permission_args,
        ]
        if extra:
            flags.extend(extra.split())
        return flags

    def run_print(self, prompt: str, log_path: Path) -> int:
        """Initial invocation: ``claude --print <prompt>`` (run_claude_print)."""
        cmd = [*self._base_command(), "--print", prompt, *self._common_flags()]
        return self._run(cmd, log_path)

    def run_continue(self, prompt: str, log_path: Path) -> int:
        """Continuation: ``claude --continue --print <prompt>`` (run_claude_continue)."""
        cmd = [
            *self._base_command(),
            "--continue",
            "--print",
            prompt,
            *self._common_flags(),
        ]
        return self._run(cmd, log_path)

    def _run(self, cmd: list[str], log_path: Path) -> int:
        """Run claude in the workspace, bounded by remaining wall time, tee output.

        Returns the claude exit code, or 124 if the wall-time budget is already
        exhausted (matching the harness convention).
        """
        limit = self.contract.remaining_wall_time()
        if limit <= 0:
            print("wall-time budget exhausted", file=sys.stderr)
            return 124

        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Match `(...) | tee "$log_path"` in run_session.sh exactly: only stdout
        # is piped to tee (→ log + terminal); stderr is inherited and goes to the
        # terminal only, never into the log.
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.contract.work_dir),
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
            )
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    # The log file is the durable artifact; stdout is only a
                    # best-effort live mirror. In long RSIBench-Data runs stdout
                    # may be a non-blocking pipe, so never let mirroring crash
                    # the agent or wedge the harness.
                    log.write(line)
                    try:
                        sys.stdout.write(line)
                    except BlockingIOError:
                        pass
                rc = proc.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                # Match `timeout --signal=TERM --kill-after=30s`: TERM, then KILL.
                proc.terminate()
                try:
                    rc = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = 124
        return rc

    @staticmethod
    def is_available() -> bool:
        return shutil.which("claude") is not None


@dataclass
class CodexDriver:
    """Builds and runs native ``codex exec`` invocations."""

    contract: WorkspaceContract
    env: dict = field(default_factory=lambda: dict(os.environ))

    def __post_init__(self) -> None:
        self._proxy_process: subprocess.Popen | None = None
        self._proxy_url = self.env.get("CODEX_RESPONSES_PROXY_URL", "")
        self._provider_id = self.env.get("CODEX_PROVIDER_ID", "rsibench_data_proxy")
        self._sandbox_mode = self.env.get("CODEX_SANDBOX_MODE", "danger-full-access")
        self._reasoning_effort = self.env.get("CODEX_MODEL_REASONING_EFFORT", "")
        self._codex_home = Path(
            self.env.get(
                "CODEX_HOME_DIR",
                str(self.contract.work_dir.parent / "runner_state" / "codex_home"),
            )
        )
        self._codex_home.mkdir(parents=True, exist_ok=True)
        self.env["CODEX_HOME"] = str(self._codex_home)

    @property
    def cli_name(self) -> str:
        return "Codex"

    @staticmethod
    def is_available() -> bool:
        return shutil.which("codex") is not None

    def close(self) -> None:
        if self._proxy_process is None:
            return
        if self._proxy_process.poll() is None:
            self._proxy_process.terminate()
            try:
                self._proxy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proxy_process.kill()
                self._proxy_process.wait(timeout=5)
        self._proxy_process = None

    def _proxy_script(self) -> Path:
        return self.contract.repo_root / "runner" / "lib" / "codex_responses_proxy.py"

    def _ensure_proxy(self) -> None:
        if self._proxy_url:
            return

        base_url = self.env.get("CODEX_BASE_URL")
        api_key = self.env.get("CODEX_API_KEY")
        if not base_url or not api_key:
            raise RuntimeError(
                "CODEX_BASE_URL and CODEX_API_KEY must both be set for Codex"
            )

        proxy_script = self._proxy_script()
        if not proxy_script.is_file():
            self._proxy_url = base_url
            return

        ready_path = Path(
            self.env.get(
                "CODEX_PROXY_READY",
                str(self.contract.work_dir.parent / "runner_state" / "codex_responses_proxy.env"),
            )
        )
        log_path = Path(
            self.env.get(
                "CODEX_PROXY_LOG",
                str(self.contract.work_dir.parent / "runner_state" / "codex_responses_proxy.log"),
            )
        )
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.unlink(missing_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            self._proxy_process = subprocess.Popen(
                [
                    sys.executable,
                    str(proxy_script),
                    "--ready-file",
                    str(ready_path),
                    "--target-base-url",
                    base_url,
                    "--api-key-env",
                    "CODEX_API_KEY",
                    "--api-version",
                    self.env.get("CODEX_RESPONSES_API_VERSION", "2025-04-01-preview"),
                    "--model",
                    self.contract.model,
                ],
                cwd=str(self.contract.repo_root),
                env=self.env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        for _ in range(100):
            if ready_path.is_file() and ready_path.stat().st_size:
                values = {}
                for line in ready_path.read_text(encoding="utf-8").splitlines():
                    key, separator, value = line.partition("=")
                    if separator:
                        values[key] = value
                self._proxy_url = values.get("CODEX_RESPONSES_PROXY_URL", "")
                if self._proxy_url:
                    return
            if self._proxy_process.poll() is not None:
                raise RuntimeError(f"Codex Responses proxy exited with {self._proxy_process.returncode}")
            time.sleep(0.1)
        raise RuntimeError("Codex Responses proxy did not become ready")

    def _config_args(self) -> list[str]:
        provider = self._provider_id
        base_url = self._proxy_url
        args = [
            "-c",
            f'model_provider="{provider}"',
            "-c",
            f'model_providers.{provider}.name="RSIBench-Data Responses Proxy"',
            "-c",
            f'model_providers.{provider}.base_url="{base_url}"',
            "-c",
            f'model_providers.{provider}.env_key="CODEX_API_KEY"',
            "-c",
            f"model_providers.{provider}.request_max_retries=0",
            "-c",
            f"model_providers.{provider}.stream_max_retries=0",
            "-c",
            f'sandbox_mode="{self._sandbox_mode}"',
            "-c",
            'approval_policy="never"',
            "-c",
            'shell_environment_policy.inherit="all"',
            "-c",
            "shell_environment_policy.ignore_default_excludes=true",
        ]
        if self._reasoning_effort:
            args.extend(["-c", f'model_reasoning_effort="{self._reasoning_effort}"'])
        return args

    def _command(
        self,
        prompt: str,
        last_message_path: Path,
        *,
        resume: bool,
        work_dir: Path | None = None,
        model: str | None = None,
    ) -> list[str]:
        work_dir = work_dir or self.contract.work_dir
        model = model or self.contract.model
        command = [
            "codex",
            "--ask-for-approval",
            "never",
            "--cd",
            str(work_dir),
            "exec",
        ]
        if resume:
            command.append("resume")
            command.append("--last")
        command.extend(
            [
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--json",
                "--output-last-message",
                str(last_message_path),
                "--model",
                model,
                *self._config_args(),
                prompt,
            ]
        )
        return command

    def run_print(self, prompt: str, log_path: Path) -> int:
        try:
            self._ensure_proxy()
        except RuntimeError as exc:
            print(f"godel_forest: {exc}", file=sys.stderr)
            return 1
        return self._run(
            self._command(prompt, log_path.with_suffix(".last.txt"), resume=False),
            log_path,
        )

    def run_continue(self, prompt: str, log_path: Path) -> int:
        try:
            self._ensure_proxy()
        except RuntimeError as exc:
            print(f"godel_forest: {exc}", file=sys.stderr)
            return 1
        return self._run(
            self._command(prompt, log_path.with_suffix(".last.txt"), resume=True),
            log_path,
        )

    def _run(self, cmd: list[str], log_path: Path) -> int:
        limit = self.contract.remaining_wall_time()
        if limit <= 0:
            print("wall-time budget exhausted", file=sys.stderr)
            return 124

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.contract.work_dir),
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
            )
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    try:
                        sys.stdout.write(line)
                    except BlockingIOError:
                        pass
                rc = proc.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    rc = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = 124
        return rc
