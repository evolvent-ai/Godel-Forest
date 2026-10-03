"""Entry point: ``python -m godel_forest``.

Wired into RSIBench-Data by setting ``DATA_AGENT_COMMAND="python -m godel_forest"``
(or the ``godel_forest`` console script). run_session.sh invokes us once, inside
the agent workspace, with the RSIBENCH_DATA_* / ANTHROPIC_* environment exported.

v0 responsibility: keep the Claude Code session alive through the RSIBench-Data
workspace contract. The entry point also installs an evidence-driven workflow
that teaches the agent to plan, evaluate one data candidate at a time, and record
results. We still do not generate data or choose attempts in Python; the model
does that by reading the workspace and calling the provided scripts.
"""

from __future__ import annotations

import os
import sys

from .claude_driver import ClaudeDriver, CodexDriver
from .contract import ContractError, load_contract
from .evidence_workflow import install_evidence_workflow
from .loop import run_loop
from .multiagent.config import load_multiagent_config
from .multiagent.manager import run_multiagent_loop


def main(argv: list[str] | None = None) -> int:
    try:
        contract = load_contract()
    except ContractError as exc:
        print(f"godel_forest: contract error: {exc}", file=sys.stderr)
        return 2

    harness = os.environ.get("DATA_AGENT_HARNESS", "claude-code").strip().lower()
    if harness == "codex":
        driver_class = CodexDriver
        cli_name = "codex"
    elif harness == "claude-code":
        driver_class = ClaudeDriver
        cli_name = "claude"
    else:
        print(f"godel_forest: unsupported DATA_AGENT_HARNESS={harness}", file=sys.stderr)
        return 1

    if not driver_class.is_available():
        print(
            f"godel_forest: {cli_name} CLI not found on PATH. Install the CLI or "
            "point DATA_AGENT_COMMAND at a different harness.",
            file=sys.stderr,
        )
        return 1

    print(f"godel_forest evidence workflow starting (model={contract.model})")
    print(f"  workspace={contract.work_dir}")
    print(f"  budget_state={contract.budget_state_path}")

    driver = driver_class(contract=contract)
    multi_config = load_multiagent_config(default_model=contract.model)
    try:
        if multi_config.enabled:
            print(
                "godel_forest multi-agent orchestration starting "
                f"(agents={','.join(multi_config.agent_ids)}, model={multi_config.model})"
            )
            rc = run_multiagent_loop(contract, driver, multi_config)
        else:
            result = install_evidence_workflow(contract.work_dir)
            if result.created:
                created = ", ".join(path.name for path in result.created)
                print(f"  installed_workflow={created}")
            if result.updated:
                updated = ", ".join(
                    str(path.relative_to(contract.work_dir)) for path in result.updated
                )
                print(f"  updated_workflow={updated}")
            if result.removed:
                removed = ", ".join(
                    str(path.relative_to(contract.work_dir)) for path in result.removed
                )
                print(f"  removed_legacy_workflow={removed}")
            rc = run_loop(contract, driver)
    finally:
        close = getattr(driver, "close", None)
        if close is not None:
            close()

    if contract.final_selection_exists():
        print(f"final_selection={contract.final_selection_path}")
    else:
        print(
            "godel_forest: no final selection was submitted before stopping",
            file=sys.stderr,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
