# Workspace Contract

This file is the **single source of truth** for how `godel_forest` couples to
RSIBench-Data. The benchmark harness (`runner/run_session.sh`) invokes us through
its `DATA_AGENT_COMMAND` seam; it does not import our code, and we do not import
its code. The entire coupling surface is the environment variables, scripts, and
JSON files described below.

If RSIBench-Data changes any of these, `godel_forest` must be updated to match —
and `contract.py` fails fast at startup when the expected pieces are missing, so
a contract drift surfaces immediately instead of mid-run.

> Reference points are line ranges in RSIBench-Data's `runner/run_session.sh` as
> of `main @ 4a9de17`. They are guidance, not a guarantee; re-verify when the
> harness changes.

## Invocation

The harness runs our command exactly once, inside the agent workspace:

```bash
(cd "$WORK_DIR"; bash -lc "$DATA_AGENT_COMMAND")
```

(run_session.sh ~636-639). Because it is invoked only once, the re-prompt loop
that the built-in `claude-code` harness provides (~647-672) does **not** apply
to us. Owning that loop is our responsibility — see `loop.py`.

## Environment provided to us

Exported to the subprocess (run_session.sh ~509-517, plus `.env` loaded via
`load_env.sh` with `export`):

| Variable | Meaning | Used for |
|----------|---------|----------|
| `RSIBENCH_DATA_WORK_DIR` | Absolute path to `agent_workspace/`. Our cwd. | Locate scripts, derive session dir. |
| `RSIBENCH_DATA_REPO_ROOT` | RSIBench-Data checkout root. | Sanity check only. |
| `RSIBENCH_DATA_PROMPT_PATH` | Path to the generated agent prompt. | Initial `--print` prompt text. |
| `ANTHROPIC_BASE_URL` plus API key or auth token | Claude API config. | Drives current Claude auth handling. |
| `ANTHROPIC_MODEL` | Default agent model. | Model passed to `claude` (see below). |
| `DATA_AGENT_ROLL_MODEL` | Model for trajectory rolling (future use). | Not used in v0. |
| `BASH_MAX_TIMEOUT_MS` | Max bash timeout the harness set. | Informational. |

**Not necessarily exported** (local shell variables in the harness), so we derive them
ourselves:

- `DATA_AGENT_MODEL` — local in run_session.sh; we fall back to `ANTHROPIC_MODEL`,
  exactly as the harness derives its own default (~12-15).
- `MIN_REMAINING_SEC` — local; default `1800` (~29).

`budget_state.json` is not exported by path. We derive it as
`<WORK_DIR>/../runner_state/budget_state.json` (the session layout the harness
creates).

## Scripts we may call

Generated into the workspace by the harness. We **call** them; we never read or
rewrite them (that would trip the tamper audit). Presence is checked at startup:

- `timer.sh` — wall-time readout.
- `budget_status.sh` — budget + artifact summary (captured for the continue prompt).
- `run_attempt.sh <attempt_id>` — runs the immutable train→eval chain.
- `final_submit.sh <attempt_id>` — writes `final_selection.json`.

Gödel Forest additionally injects its own `continue_eval.sh <attempt_id>` helper.
It does not replace or rewrite an RSIBench-Data script: it reads the checkpoint
created by a completed subset attempt, invokes the existing evaluation backend
for the remaining task IDs, and records the merged full-coverage result under
the same shared budget lock.

## Files we read

- `runner_state/budget_state.json` — `started_at`, `budget.wall_time_budget_sec`,
  `remaining_tinker_cost_usd`. Drives our budget gates, computed exactly like the
  harness `remaining_wall_time` / `remaining_cost` helpers (~519-538).
- `<WORK_DIR>/final_selection.json` — existence is the completion signal. The
  harness also treats its presence as "done" (~655, ~698).

## Files we write directly

This branch installs a small Gödel Forest-owned workflow layer before launching
Claude. These supplemental files live in the agent workspace and are intentionally
separate from RSIBench-Data-owned instruction files and harness scripts:

- `.claude/skills/human-research/SKILL.md` — human-style benchmark, failure, data-source, and training-dynamics research guidance.
- `.claude/skills/candidate-reflection/SKILL.md` — candidate hypothesis and validation checkpoint.
- `.claude/skills/diagnostic-eval-plan/SKILL.md` — bounded diagnostic evaluation planning.
- `.claude/skills/attempt-review/SKILL.md` — post-evaluation evidence review and decision workflow.
- `GODEL_FOREST_FLOW.md` — evidence-driven candidate workflow guide.
- `DATA_PLAN.md` — starter planning template, written only if absent in single-agent mode.
- `ATTEMPT_REVIEW.md` — starter attempt-review template, written only if absent in single-agent mode.
- `CANDIDATES.jsonl` — candidate ledger placeholder, written only if absent in single-agent mode.
- `continue_eval.sh` — managed checkpoint-continuation entrypoint refreshed from
  the package.

Managed workflow instructions and skills are refreshed from the installed
package, while agent-authored plans, reviews, and ledgers are preserved. We still
never rewrite `RUN_CONTRACT.md`, `AGENT_RULES.md`,
`DATA_GENERATION.md`, `MINI_SWE_DATA_FORMAT.md`, `EVAL_ATTEMPT_LOOP.md`, or any
workspace script generated by RSIBench-Data.

## Multi-agent orchestration files

When `GODEL_FOREST_MODE=multi` (or explicit multi-agent IDs/count) is set,
`godel_forest` creates additional Gödel Forest-owned state under the workspace:

- `.godel_forest/public/` — shared public notes, messages, candidate summaries,
  probe results, attempt summaries, skills, roles, logs, leaderboard, and final
  recommendations.
- `.godel_forest/private/` — manager state, process/session state, and lock files.
  Agent workers are guarded from reading or writing this tree.
- `agents/<agent_id>/` — one private working directory per Claude worker, with
  `.claude/` symlinks into `.godel_forest/public/`, plus private
  `DATA_PLAN.md`, `ATTEMPT_REVIEW.md`, and `CANDIDATES.jsonl` files.

In multi-agent mode, the three mutable workflow files are created and read only under
each worker's private directory. Any pre-existing root-level copies from an older
run are ignored and are not migrated or deleted. Cross-agent summaries are
published through `.godel_forest/public/` instead.

Workers may run training, diagnostic subset attempts through the standard
`run_attempt.sh --task-ids-file` interface, promote an unchanged subset attempt
with `continue_eval.sh <same_attempt_id>`, and run new full official attempts, but must
not run `final_submit.sh` or write `final_selection.json`; final submit is
manager-only. A subset remains diagnostic until continuation covers the task-ID
complement; then the same attempt is upgraded to full and becomes eligible for
manager final selection. RSIBench-Data records active owner reservations in
`budget_state.json`; each worker may own one active attempt while different
workers run concurrently. Gödel Forest and RSIBench-Data use the same lock at
`runner_state/budget_state.lock` for state transactions.

After a completed attempt, a worker may write a valid terminal recommendation
under `.godel_forest/public/final_recommendations/`; this ends only that worker,
not the run. The manager records an equivalent terminal recommendation when a
worker remains in an error state for `GODEL_FOREST_AGENT_ERROR_GRACE_SEC`
(default: 300 seconds). Once all configured workers are terminal and no active
attempt remains, the manager rereads `budget_state.json` and submits the current
highest-scoring completed full-set attempt. Subset results and the base-model
baseline are never final-selectable.

We never write training data ourselves in v0; Claude does, via the scripts.
The outputs that matter to the harness after we exit:

- `<WORK_DIR>/final_selection.json` — must exist, or the harness reports the run
  as failed (~698-704).
- `<session>/agent_initial.jsonl`, `agent_continue_<n>.jsonl` — our tee'd logs,
  mirroring the harness log names.

## claude CLI invocation (v0 fidelity)

We reproduce the harness exactly (run_session.sh ~568-634):

- Auth: with `ANTHROPIC_AUTH_TOKEN`, preserve the token and remove a competing
  API key; with `ANTHROPIC_API_KEY`, remove the token and add
  `--bare --setting-sources project,local`.
- `export ANTHROPIC_MODEL=<agent model>`.
- Flags: `--print` (or `--continue --print`), `--model <model>`, `--verbose`,
  `--output-format stream-json`, `--thinking-display summarized`,
  `--allowedTools <CLAUDE_ALLOWED_TOOLS or default>`, plus permission args
  (`--dangerously-skip-permissions`, or `--permission-mode acceptEdits` as root,
  or `--permission-mode <CLAUDE_PERMISSION_MODE>`), plus `CLAUDE_CODE_ARGS`.
- Each call is bounded by remaining wall time (TERM then KILL after 30s).

## What is NOT part of the contract

- The content of the instruction `.md` files and benchmark assets — owned by
  RSIBench-Data, read by Claude, not by us.
- The immutable train/eval chain, the run_config whitelist, and the tamper audit
  — enforced entirely on the harness side. We do not touch them.
