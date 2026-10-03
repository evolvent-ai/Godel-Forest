"""Prompt and per-agent instruction rendering for parallel workers."""

from __future__ import annotations

from pathlib import Path

from godel_forest.evidence_workflow import (
    append_initial_workflow_note,
    render_continue_workflow_note,
)


PARALLEL_INITIAL_NOTE = """

---

Parallel workspace note: this Claude session is one independent replica of the
same evidence-driven RSIBench-Data data-agent loop. Follow the RSIBench-Data prompt
above and `{flow}` exactly as in the single-agent workflow.

Use worker id `{agent_id}` only as a neutral collision-avoidance prefix for files
and attempt IDs. Do not infer a role, lane, or specialization from this id.

Write durable run state under the shared worker-facing `.claude` workspace at
`{worker_claude}` so the manager and future replicas can inspect it: notes,
messages, candidate summaries, diagnostic results, attempt summaries, and final
recommendations. The current workflow skills are available under
`{worker_claude}/skills/`; follow the mandatory `human-research`,
`candidate-reflection`, `diagnostic-eval-plan`, and `attempt-review` checkpoints
described in `{flow}`.
Before designing or substantially revising any training-data candidate, first
inspect the shared public workspace for relevant findings from other workers,
and incorporate applicable conclusions into your private DATA_PLAN.md.

Your private scratch workspace is: `{worker_workspace}`
The main RSIBench-Data workspace is: `{work_dir}`
Your private workflow state files are:
`{data_plan}`, `{attempt_review}`, and `{candidates}`.
Use attempt IDs prefixed with `{agent_id}_` only to avoid collisions.
In this multi-agent mode, interpret the base RSIBench-Data instruction "at most
one attempt at a time" as one active attempt per worker. RSIBench-Data enforces
this through the worker owner reservation: different workers may evaluate in
parallel, but this worker cannot reserve a second attempt until its active one
finishes.
Plan the shared budget across the full evolution loop rather than spending
heavily at the start. It is reasonable to use a somewhat smaller, still
informative budget for early attempts so that later corrections and follow-up
attempts retain meaningful headroom. Avoid both extremes: do not jump
immediately to the largest expensive configuration, and do not undersize the
attempt to the point that its result is uninformative. Choose enough training
and evaluation scale to produce a reliable signal.
After reviewing the first attempt, continue the normal evidence-driven
evolution process. All workers remain eligible to launch subsequent attempts;
use the shared leaderboard and attempt evidence to decide the next step.
For a diagnostic subset, write a non-empty JSON list of Harbor task names to
`{attempts_dir}/<attempt_id>/eval_task_ids.json`, then run the standard command in
the foreground as `bash {run_attempt_script} <attempt_id> --task-ids-file
{attempts_dir}/<attempt_id>/eval_task_ids.json`. If that subset passes and the training
data and run configuration are unchanged, do not create a new attempt and do not
call `{run_attempt_script}` again. Continue the same attempt in the foreground with
`bash {continue_eval_script} <attempt_id>`; it reuses the existing checkpoint,
evaluates only the remaining configured tasks, and upgrades that same attempt to
full evaluation coverage. Use `bash {run_attempt_script} <new_attempt_id>` without
`--task-ids-file` only when intentionally training a new candidate directly for a
full eval. Never launch `{run_attempt_script}` or `{continue_eval_script}` with
`nohup`, `&`, or another background wrapper.
Do not write `{work_dir}/final_selection.json`; the manager submits the best completed
official attempt once all workers have terminated.
After each completed attempt, reread the shared leaderboard. If you decide that
you should stop exploring, write a terminal recommendation to
`{worker_claude}/final_recommendations/{agent_id}.json` with this shape:
`{{"agent_id":"{agent_id}","decision":"submit","source":"agent","reason_type":"agent_decision","reason":"...","attempt_id":"<completed full attempt>","score":<score>,"evaluation_scope":"full"}}`.
The referenced attempt must be completed and full-set. This recommendation ends
your worker only; never run `final_submit.sh` yourself. If you can still make
progress, do not write a terminal recommendation and continue to the next attempt.
"""


RESUME_PARALLEL_NOTE = (
    " Keep using worker id `{agent_id}` only as a neutral collision-avoidance "
    "prefix. Write durable notes, candidate evidence, diagnostic results, attempt "
    "summaries, and final recommendations under the shared worker-facing `.claude` "
    "workspace at `{worker_claude}`. The current workflow skills are available under "
    "`{worker_claude}/skills/`; continue using the required human-research, "
    "candidate-reflection, and attempt-review checkpoints described in `{flow}`. "
    "Keep this worker's mutable workflow state in `{data_plan}`, `{attempt_review}`, "
    "and `{candidates}`; do not use a shared root-level copy. "
    "The RSIBench-Data one-at-a-time rule applies per worker in this multi-agent run; "
    "owner reservations allow different workers to run concurrently while preventing "
    "this worker from starting a duplicate active attempt. "
    "Use `{run_attempt_script}` in the foreground to train a new or changed candidate; "
    "add `--task-ids-file` for its diagnostic subset or omit it to train and evaluate "
    "a new candidate on the full set. If an unchanged candidate's subset passes, do "
    "not create a new attempt or retrain it: run `bash {continue_eval_script} "
    "<same_attempt_id>` in the foreground to reuse its checkpoint and evaluate only "
    "the remaining tasks. "
    "Do not write `{work_dir}/final_selection.json`; the manager submits the best completed "
    "official attempt once all workers have terminated. After reviewing the shared "
    "leaderboard after an attempt, a worker that is done may write a valid terminal "
    "recommendation to `{worker_claude}/final_recommendations/{agent_id}.json`; do not "
    "run `final_submit.sh` yourself."
)


def _worker_paths(
    agent_id: str,
    work_dir: Path | None = None,
    worker_workspace: Path | None = None,
) -> dict[str, Path]:
    work_dir = Path.cwd() if work_dir is None else Path(work_dir)
    work_dir = work_dir.resolve()
    worker_workspace = (
        work_dir / "agents" / agent_id
        if worker_workspace is None
        else Path(worker_workspace)
    ).resolve()
    worker_claude = worker_workspace / ".claude"
    return {
        "work_dir": work_dir,
        "worker_workspace": worker_workspace,
        "worker_claude": worker_claude,
        "flow": work_dir / "GODEL_FOREST_FLOW.md",
        "data_plan": worker_workspace / "DATA_PLAN.md",
        "attempt_review": worker_workspace / "ATTEMPT_REVIEW.md",
        "candidates": worker_workspace / "CANDIDATES.jsonl",
        "attempts_dir": work_dir / "attempts",
        "run_attempt_script": work_dir / "run_attempt.sh",
        "continue_eval_script": work_dir / "continue_eval.sh",
    }


def _format_paths(paths: dict[str, Path]) -> dict[str, str]:
    return {key: str(value) for key, value in paths.items()}


def render_agent_workspace_doc(
    agent_id: str,
    work_dir: Path | None = None,
    worker_workspace: Path | None = None,
) -> str:
    """Render ``GODEL_FOREST_AGENT.md`` for one worker workspace."""
    paths = _worker_paths(agent_id, work_dir, worker_workspace)
    worker_claude = paths["worker_claude"]
    root = paths["work_dir"]
    return f"""# RSIBench-Data Parallel Worker: {agent_id}

This workspace is one independent replica of the same RSIBench-Data data-agent
task. Follow the main RSIBench-Data prompt and `{paths['flow']}` as closely as the
single-agent evidence workflow does.

`{agent_id}` is only a neutral worker id for collision avoidance. Do not infer a
role, lane, or specialization from it.

## Your scratch workspace

Your private scratch workspace is the current directory:

```text
{paths['worker_workspace']}
```

Use it for temporary scripts, local analysis, candidate drafts, and intermediate
data. Do not depend on conversation context for durable state.

## Shared public workspace

Write durable run state through the shared public `.claude` links:

```text
{worker_claude / 'notes'}
{worker_claude / 'messages'}
{worker_claude / 'candidates'}
{worker_claude / 'probes'}
{worker_claude / 'attempts'}
{worker_claude / 'final_recommendations'}
```

Use these for plans, evidence records, candidate summaries, diagnostic results,
attempt summaries, and final recommendations so the manager and future replicas
can inspect what happened.

The current evidence-workflow skills are available at:

```text
{worker_claude / 'skills/human-research/SKILL.md'}
{worker_claude / 'skills/candidate-reflection/SKILL.md'}
{worker_claude / 'skills/diagnostic-eval-plan/SKILL.md'}
{worker_claude / 'skills/attempt-review/SKILL.md'}
```

Use them under the conditions described in `{paths['flow']}` before planning,
generating, evaluating, or revising a meaningful candidate.

Your mutable workflow state is private to this worker:

```text
{paths['data_plan']}
{paths['attempt_review']}
{paths['candidates']}
```

Do not read or write root-level copies of these files. Publish cross-agent
summaries through the shared `.claude` links instead.

## Main RSIBench-Data workspace

The official RSIBench-Data workspace is:

```text
{root}
```

When `{paths['flow']}` names a workspace file without an absolute path, resolve
that name under `{root}` rather than under the worker's current directory.

Read the RSIBench-Data instructions there before acting:

```text
{root / 'RUN_CONTRACT.md'}
{root / 'AGENT_RULES.md'}
{root / 'DATA_GENERATION.md'}
{root / 'MINI_SWE_DATA_FORMAT.md'}
{root / 'EVAL_ATTEMPT_LOOP.md'}
{paths['flow']}
```

Official attempt artifacts must live under:

```text
{paths['attempts_dir']}/<attempt_id>/
```

Use attempt IDs prefixed with your neutral worker id, for example:

```text
{agent_id}_001
{agent_id}_002
```

For a diagnostic subset, write a non-empty JSON list of Harbor task names to:

```text
{paths['attempts_dir']}/<attempt_id>/eval_task_ids.json
```

Then invoke the same standard attempt entrypoint used for full evaluation:

```bash
bash {paths['run_attempt_script']} <attempt_id> \
  --task-ids-file {paths['attempts_dir']}/<attempt_id>/eval_task_ids.json
```

The option path is absolute. Omit `--task-ids-file` only when intentionally
training a new candidate directly on the full configured task set. A subset
attempt is diagnostic evidence, not yet a final-selectable full score.

If the subset passes and neither the training data nor `run_config.json` changes,
continue the same attempt instead of copying it to a new attempt ID:

```bash
bash {paths['continue_eval_script']} <attempt_id>
```

`{paths['continue_eval_script']}` reads the checkpoint produced by the original subset attempt,
evaluates only the remaining configured Harbor tasks, merges the task-level
results and sampling cost, and upgrades the same attempt to full evaluation
coverage. It does not call `train.sh`. If the subset fails or the data/config is
changed, create a new attempt ID and use `{paths['run_attempt_script']}` again.

Run `{paths['run_attempt_script']}` and `{paths['continue_eval_script']}` in the foreground. Never use `nohup`,
append `&`, or otherwise detach either script. Different workers may run their own
foreground attempts concurrently, but a worker must not start a second attempt
while its current one is still running. RSIBench-Data records an owner reservation
before training and will reject duplicate active attempts from this worker or
attempts that exceed the currently unreserved training budget.

You may generate data, train models, run diagnostic subsets, and run full official
attempts. Do not write `{root / 'final_selection.json'}`; the manager will submit the best
completed full-evaluation attempt once near the end of the run.
"""


def render_initial_prompt(
    agent_id: str,
    work_dir: Path,
    base_prompt: str,
    worker_workspace: Path | None = None,
) -> str:
    """Render the initial prompt passed to a worker Claude session."""
    paths = _worker_paths(agent_id, work_dir, worker_workspace)
    prompt = append_initial_workflow_note(
        base_prompt.rstrip("\n"),
        work_dir=paths["work_dir"],
        state_dir=paths["worker_workspace"],
    )
    return prompt + PARALLEL_INITIAL_NOTE.format(
        agent_id=agent_id,
        **_format_paths(paths),
    )


def render_resume_prompt(
    agent_id: str,
    work_dir: Path | None = None,
    worker_workspace: Path | None = None,
) -> str:
    """Render a restart/resume prompt for a worker Claude session."""
    paths = _worker_paths(agent_id, work_dir, worker_workspace)
    return (
        "Continue the same RSIBench-Data Tinker run."
        f"{render_continue_workflow_note(paths['work_dir'], paths['worker_workspace'])}"
        f"{RESUME_PARALLEL_NOTE.format(agent_id=agent_id, **_format_paths(paths))}"
    )
