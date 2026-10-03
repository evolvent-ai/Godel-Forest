"""Re-prompt loop.

The RSIBench-Data harness invokes ``DATA_AGENT_COMMAND`` only once, so the loop
that keeps Claude going until it submits a final selection must live here. This
branch keeps the v0 outer loop mechanics (initial ``--print``, budget-aware
``--continue``, final-selection stop condition) and appends a concise
evidence-workflow reminder to the prompts.
"""

from __future__ import annotations

from pathlib import Path

from .claude_driver import ClaudeDriver
from .contract import WorkspaceContract
from .evidence_workflow import CONTINUE_WORKFLOW_NOTE, append_initial_workflow_note

# Continuation prompt from run_session.sh with one Gödel Forest-owned workflow
# reminder appended. The budget/submission instructions remain the v0 harness
# prompt; the appended note only points Claude back to workspace evidence files.
CONTINUE_PROMPT_TEMPLATE = (
    "Continue the same RSIBench-Data Tinker run. You have not submitted a final "
    "attempt yet. Current budget status:\\n\\n{status}\\n\\nDo not background "
    "run_attempt.sh with nohup, &, disown, setsid, watcher scripts, or "
    "task/waiter mechanisms. Run at most one attempt at a time in the foreground "
    "as bash run_attempt.sh <attempt_id>, keep the command active until it "
    "returns, then inspect budget_status.sh and artifacts. If an attempt is "
    "already running, wait for it to finish instead of launching a duplicate. If "
    "a completed best attempt exists and remaining wall time is under one hour, "
    "immediately run bash final_submit.sh <best_attempt_id>. In the final two "
    "hours, do not launch a heavier or speculative attempt over a completed "
    "best; only run a new attempt if the measured ETA plus a 30-minute submit "
    "buffer fits. Finish by running bash final_submit.sh <attempt_id> before "
    "budget is exhausted."
    f"{CONTINUE_WORKFLOW_NOTE}"
)


def _budget_status(contract: WorkspaceContract, driver: ClaudeDriver) -> str:
    """Run budget_status.sh and capture its output for the continuation prompt.

    Mirrors ``STATUS="$(bash budget_status.sh 2>&1 || true)"`` — failures are
    swallowed and reported inline rather than aborting the loop.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["bash", str(contract.script("budget_status.sh"))],
            cwd=str(contract.work_dir),
            env=driver.env,
            capture_output=True,
            text=True,
        )
        return (result.stdout + result.stderr).strip()
    except OSError as exc:  # pragma: no cover - defensive
        return f"(budget_status.sh failed: {exc})"


def run_loop(contract: WorkspaceContract, driver: ClaudeDriver) -> int:
    """Drive Claude until a final selection exists or budget runs out.

    Returns the exit code of the last claude invocation.
    """
    session_dir = contract.work_dir.parent
    # Preserve the RSIBench-Data prompt and append only a supplemental pointer to
    # Gödel Forest's workflow guide.
    prompt = append_initial_workflow_note(
        contract.prompt_path.read_text(encoding="utf-8").rstrip("\n")
    )

    rc = driver.run_print(prompt, session_dir / "agent_initial.jsonl")
    cli_name = getattr(driver, "cli_name", "Claude Code")
    print(f"initial {cli_name} exit code: {rc}")

    iteration = 1
    while not contract.final_selection_exists():
        remaining_wall = contract.remaining_wall_time()
        if remaining_wall <= contract.min_remaining_sec:
            break
        if contract.remaining_cost() <= 0:
            break

        status = _budget_status(contract, driver)
        continue_prompt = CONTINUE_PROMPT_TEMPLATE.format(status=status)
        rc = driver.run_continue(
            continue_prompt, session_dir / f"agent_continue_{iteration}.jsonl"
        )
        print(f"continuation {iteration} {cli_name} exit code: {rc}")
        iteration += 1

    return rc
