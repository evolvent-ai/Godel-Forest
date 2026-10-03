"""Install an evidence-driven workflow into an RSIBench-Data workspace.

The workflow teaches an agent to define a task contract, plan before acting,
work on one candidate at a time, validate outcomes, record evidence, and make
explicit promote/revise/reject decisions. It supplements the existing runtime
without changing the underlying RSIBench-Data execution loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Iterator

FLOW_FILENAME = "GODEL_FOREST_FLOW.md"
DATA_PLAN_FILENAME = "DATA_PLAN.md"
ATTEMPT_REVIEW_FILENAME = "ATTEMPT_REVIEW.md"
CANDIDATES_FILENAME = "CANDIDATES.jsonl"
WORKFLOW_STATE_PATHS = frozenset(
    {
        Path(DATA_PLAN_FILENAME),
        Path(ATTEMPT_REVIEW_FILENAME),
        Path(CANDIDATES_FILENAME),
    }
)
CONTINUE_EVAL_SCRIPT_PATH = Path("continue_eval.sh")
HUMAN_RESEARCH_SKILL_PATH = Path(".claude/skills/human-research/SKILL.md")
CANDIDATE_REFLECTION_SKILL_PATH = Path(".claude/skills/candidate-reflection/SKILL.md")
ATTEMPT_REVIEW_SKILL_PATH = Path(".claude/skills/attempt-review/SKILL.md")
DIAGNOSTIC_EVAL_SKILL_PATH = Path(".claude/skills/diagnostic-eval-plan/SKILL.md")
WORKFLOW_SKILL_PATHS = (
    HUMAN_RESEARCH_SKILL_PATH,
    CANDIDATE_REFLECTION_SKILL_PATH,
    ATTEMPT_REVIEW_SKILL_PATH,
    DIAGNOSTIC_EVAL_SKILL_PATH,
)
MANAGED_ASSET_PATHS = (
    Path(FLOW_FILENAME),
    CONTINUE_EVAL_SCRIPT_PATH,
    *WORKFLOW_SKILL_PATHS,
)
LEGACY_MANAGED_PATHS = (
    Path(".claude/skills/deepresearch/SKILL.md"),
    Path(".claude/skills/deep-research/SKILL.md"),
)

ASSET_PACKAGE = "godel_forest.workspace_assets"

INITIAL_WORKFLOW_NOTE = (
    "\n\n---\n\n"
    "Supplemental Gödel Forest workflow: read `GODEL_FOREST_FLOW.md` in this "
    "workspace and use it as guidance for an evidence-driven data-candidate "
    "loop. It does not replace the RSIBench-Data instructions above.\n\n"
    "Do not reuse existing datasets, SFT records, demonstrations, or trajectories "
    "as training data; generate new examples through this run's pipeline.\n\n"
    "Before using this supplemental workflow, read the RSIBench-Data workspace "
    "instruction files and make sure the `seed_factories` guidance in "
    "`DATA_GENERATION.md` is reflected in `DATA_PLAN.md` before settling on a "
    "main data generation strategy. Use evaluation context only within the "
    "RSIBench-Data rules and follow the benchmark-generic canonical item-ID "
    "overlap boundary documented in `GODEL_FOREST_FLOW.md`.\n\n"
    "Mandatory first step: before creating or filling `DATA_PLAN.md`, generating "
    "training data, or running `run_attempt.sh`, use the `/human-research` skill. "
    "If the slash command/tool is not available, read `.claude/skills/human-research/SKILL.md` "
    "and apply its research guidance. Record the result as a `Human research note` "
    "inside `DATA_PLAN.md` before launching a materially new candidate.\n\n"
    "Before generating or substantially revising a candidate, use `/candidate-reflection` "
    "(or read `.claude/skills/candidate-reflection/SKILL.md`) to write one explicit "
    "candidate hypothesis and candidate-validation plan. Prefer authentic, "
    "source-grounded, execution-verified supervision that preserves realistic "
    "difficulty, edge cases, and solution behavior over ungrounded synthetic "
    "batches. Keep the candidate compact: "
    "each retained example should add distinct verified behavioral information, "
    "not merely increase record count. Validate the actual training dataset before "
    "launching an attempt.\n\n"
    "After any meaningful completed evaluation, use `/attempt-review` (or read "
    "`.claude/skills/attempt-review/SKILL.md`) to update `ATTEMPT_REVIEW.md` and "
    "`CANDIDATES.jsonl` before launching the next candidate. If a diagnostic "
    "subset passes, do not create a new attempt or call `run_attempt.sh` again "
    "for the unchanged candidate. Run `bash continue_eval.sh <same_attempt_id>` "
    "to reuse its checkpoint and evaluate only the remaining tasks."
)

CONTINUE_WORKFLOW_NOTE = (
    " Continue the evidence-driven workflow from `GODEL_FOREST_FLOW.md`: review "
    "`DATA_PLAN.md`, `ATTEMPT_REVIEW.md`, and `CANDIDATES.jsonl`; base the next "
    "candidate on diagnosed failure modes rather than topic similarity alone; prefer "
    "validated, behavior-matching supervision over scale or complexity; and update "
    "the written evidence trail before promoting, revising, rejecting, or replacing a candidate. "
    "Use `run_attempt.sh` for training a new or changed candidate. If an unchanged "
    "candidate's diagnostic subset passes, use `continue_eval.sh <same_attempt_id>` "
    "to reuse the trained checkpoint and evaluate only the remaining tasks."
)


@dataclass(frozen=True)
class WorkflowInstallResult:
    """Files created or preserved by the workflow installer."""

    created: tuple[Path, ...]
    updated: tuple[Path, ...]
    preserved: tuple[Path, ...]
    removed: tuple[Path, ...]


def _asset_files() -> Iterator[tuple[Path, str]]:
    """Yield workspace asset paths and contents bundled with the package."""

    def walk(node, prefix: Path = Path()) -> Iterator[tuple[Path, str]]:
        for child in node.iterdir():
            if child.name == "__pycache__":
                continue
            rel_path = prefix / child.name
            if child.is_dir():
                yield from walk(child, rel_path)
            elif child.name != "__init__.py":
                yield rel_path, child.read_text(encoding="utf-8")

    yield from walk(files(ASSET_PACKAGE))


def _write_if_absent(path: Path, content: str) -> bool:
    """Write ``content`` only when ``path`` does not already exist."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _render_state_asset_content(
    rel_path: Path,
    content: str,
    work_dir: Path,
    state_dir: Path,
) -> str:
    """Make worker-local state templates usable from a private cwd."""
    if rel_path != Path(DATA_PLAN_FILENAME) or state_dir == work_dir:
        return content
    attempts_dir = work_dir / "attempts"
    replacements = {
        "`attempts/<attempt_id>/eval_task_ids.json`": (
            f"`{attempts_dir}/<attempt_id>/eval_task_ids.json`"
        ),
        "`bash run_attempt.sh": f"`bash {work_dir / 'run_attempt.sh'}",
        "`bash continue_eval.sh": f"`bash {work_dir / 'continue_eval.sh'}",
        "`run_attempt.sh": f"`{work_dir / 'run_attempt.sh'}",
        "`continue_eval.sh": f"`{work_dir / 'continue_eval.sh'}",
    }
    for relative, absolute in replacements.items():
        content = content.replace(relative, absolute)
    return content


def install_evidence_workflow(
    work_dir: Path,
    *,
    state_dir: Path | None = None,
) -> WorkflowInstallResult:
    """Install workflow assets, optionally placing mutable state in ``state_dir``.

    Managed instructions and skills are refreshed to the packaged version while
    mutable agent state such as plans, reviews, and ledgers is preserved.
    RSIBench-Data-owned instruction files and scripts are untouched.
    """
    work_dir = Path(work_dir)
    state_dir = work_dir if state_dir is None else Path(state_dir)
    created: list[Path] = []
    updated: list[Path] = []
    preserved: list[Path] = []
    removed: list[Path] = []
    managed_paths = set(MANAGED_ASSET_PATHS)
    for rel_path, content in _asset_files():
        target_dir = state_dir if rel_path in WORKFLOW_STATE_PATHS else work_dir
        path = target_dir / rel_path
        content = _render_state_asset_content(rel_path, content, work_dir, state_dir)
        if _write_if_absent(path, content):
            created.append(path)
        elif rel_path in managed_paths and path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            updated.append(path)
        else:
            preserved.append(path)
    for rel_path in LEGACY_MANAGED_PATHS:
        path = work_dir / rel_path
        if path.is_file():
            path.unlink()
            removed.append(path)
    return WorkflowInstallResult(
        created=tuple(created),
        updated=tuple(updated),
        preserved=tuple(preserved),
        removed=tuple(removed),
    )


def _with_absolute_workflow_paths(
    note: str,
    work_dir: Path,
    state_dir: Path | None = None,
) -> str:
    """Render workflow asset references from the worker's filesystem view."""
    work_dir = Path(work_dir).resolve()
    state_dir = work_dir if state_dir is None else Path(state_dir).resolve()
    replacements = {
        "`GODEL_FOREST_FLOW.md`": f"`{work_dir / FLOW_FILENAME}`",
        "`DATA_GENERATION.md`": f"`{work_dir / 'DATA_GENERATION.md'}`",
        "`DATA_PLAN.md`": f"`{state_dir / DATA_PLAN_FILENAME}`",
        "`ATTEMPT_REVIEW.md`": f"`{state_dir / ATTEMPT_REVIEW_FILENAME}`",
        "`CANDIDATES.jsonl`": f"`{state_dir / CANDIDATES_FILENAME}`",
        "`.claude/skills/human-research/SKILL.md`": (
            f"`{work_dir / HUMAN_RESEARCH_SKILL_PATH}`"
        ),
        "`.claude/skills/candidate-reflection/SKILL.md`": (
            f"`{work_dir / CANDIDATE_REFLECTION_SKILL_PATH}`"
        ),
        "`.claude/skills/attempt-review/SKILL.md`": (
            f"`{work_dir / ATTEMPT_REVIEW_SKILL_PATH}`"
        ),
        "`bash continue_eval.sh <same_attempt_id>`": (
            f"`bash {work_dir / CONTINUE_EVAL_SCRIPT_PATH} <same_attempt_id>`"
        ),
        "`continue_eval.sh <same_attempt_id>`": (
            f"`{work_dir / CONTINUE_EVAL_SCRIPT_PATH} <same_attempt_id>`"
        ),
        "`run_attempt.sh`": f"`{work_dir / 'run_attempt.sh'}`",
    }
    for relative, absolute in replacements.items():
        note = note.replace(relative, absolute)
    return note


def append_initial_workflow_note(
    prompt: str,
    work_dir: Path | None = None,
    state_dir: Path | None = None,
) -> str:
    """Append the supplemental workflow pointer to the harness prompt."""
    note = INITIAL_WORKFLOW_NOTE
    if work_dir is not None:
        note = _with_absolute_workflow_paths(note, work_dir, state_dir)
    return prompt + note


def render_continue_workflow_note(
    work_dir: Path | None = None,
    state_dir: Path | None = None,
) -> str:
    """Render the continuation note, optionally with absolute workspace paths."""
    if work_dir is None:
        return CONTINUE_WORKFLOW_NOTE
    return _with_absolute_workflow_paths(CONTINUE_WORKFLOW_NOTE, work_dir, state_dir)
