# Gödel Forest Workflow for RSIBench-Data

This file is installed by `godel_forest` as a supplemental workflow guide. It does **not** replace the RSIBench-Data prompt or workspace instruction files; it adds an evidence-driven discipline for candidate design, evaluation, and revision.

Read the RSIBench-Data instruction files first, and make sure the `seed_factories` guidance in `DATA_GENERATION.md` is reflected in `DATA_PLAN.md`.

The point of this workflow is to keep the agent from drifting into ungrounded data generation or reproducing current evaluation items. Each candidate should be motivated by an explicit failure hypothesis, validated in a bounded way, and reviewed against observed evaluation changes.

## Evaluation boundary

For contamination control, identify the canonical task or item IDs used by the
current evaluation set and the canonical task or item IDs represented by the
training data. Normalize identifiers when the source and benchmark use different
field names. The intersection between the training IDs and current evaluation IDs
must be empty. This canonical-ID overlap check is the contamination criterion for
this supplemental workflow; do not introduce additional contamination criteria.

## Core loop

Work through the run in four stages:

1. Diagnose the benchmark and current model failures.
2. Propose one candidate data direction.
3. Validate and evaluate that candidate.
4. Review the evidence and explicitly decide promote / revise / reject, or stop
   exploring when the shared leaderboard shows no promising next move.

Repeat the loop only when the evidence trail is up to date.

## Files you own in this workflow

- `DATA_PLAN.md` — active plan, benchmark bottleneck hypothesis, candidate hypotheses, validation plans, and budget strategy.
- `ATTEMPT_REVIEW.md` — per-attempt evidence log and promote/revise/reject decisions.
- `CANDIDATES.jsonl` — machine-readable candidate ledger.
- `.claude/skills/human-research/SKILL.md` — required human-style benchmark and training research guidance.
- `.claude/skills/candidate-reflection/SKILL.md` — candidate-design checkpoint before generating or revising data.
- `.claude/skills/attempt-review/SKILL.md` — post-eval evidence review and decision workflow.

## Stage 1 — Diagnose before generating

Before creating or changing a candidate recipe:

1. Read the RSIBench-Data prompt and workspace instruction files.
2. Review `DATA_GENERATION.md` and extract the required `seed_factories` reference-method ideas.
3. Treat aggregate evaluation-dataset distribution metadata as a mandatory
   candidate-planning input when available, such as repository names and task
   counts used to identify coverage concentration.
   Use current evaluation items only as permitted by the RSIBench-Data instructions,
   and enforce the canonical-ID non-overlap boundary above for training data.
4. Write the extracted pipeline, quality-control, and aggregate coverage methods
   into `DATA_PLAN.md` before choosing a main data-generation recipe.
5. Use `/human-research`. If the slash command is unavailable, read `.claude/skills/human-research/SKILL.md` and apply its guidance.
6. Record a `Human research note` in `DATA_PLAN.md` with:
   - benchmark bottleneck hypothesis
   - dominant observed failure modes
   - candidate direction to test next
   - why it may help
   - what must be validated before scaling
   - promotion / revision / rejection signals

Do not generate data or run a materially new candidate until this note exists.

### Discipline

- Identify the dominant failure mode before expanding data scope.
- Treat the `seed_factories` references in `DATA_GENERATION.md` as required method inputs.
- Prefer behaviorally aligned supervision over topic similarity.
- Treat complexity, longer trajectories, or more elaborate reasoning as hypotheses to validate, not default improvements.
- Prefer authentic source material, real execution where applicable, independent
  outcome verification, and realistic difficulty and edge cases over ungrounded
  synthetic demonstrations.
- Treat verified behavioral information as the scarce resource. Do not use raw
  record count as a quality proxy.
- Prefer a compact, high-quality candidate dataset. Each retained example should
  add distinct, independently verified behavioral signal; remove repetitive or
  superficial variants that only increase volume.

## Stage 2 — Propose exactly one candidate

Before generating a new or revised candidate:

1. Use `/candidate-reflection` or read `.claude/skills/candidate-reflection/SKILL.md`.
2. Write one bounded candidate hypothesis in `DATA_PLAN.md`.
3. State what the candidate changes and what it intentionally keeps fixed.
4. Define validation for the candidate dataset before training or spending a full eval.

The candidate hypothesis must include a data-integrity claim: explain the real-data
grounding, how outcomes are verified, and how canonical training IDs were checked
for an empty intersection with the current evaluation IDs.

One-candidate-at-a-time means a candidate should have a clear identity. Avoid bundling many unrelated changes into one attempt unless the benchmark contract forces them together.

## Stage 3 — Validate and evaluate

For each candidate:

1. Run the smallest validation needed to confirm schema, execution, parser, or answer correctness.
2. Audit a bounded sample for data integrity: authentic source grounding, real
   observations where applicable, canonical evaluation-ID non-overlap, realistic
   task structure, correct protocol behavior, and independent outcome verification.
3. Only after validation passes, launch the attempt with `bash run_attempt.sh <attempt_id>`.
   For a diagnostic subset, add `--task-ids-file attempts/<attempt_id>/eval_task_ids.json`.
4. If that subset passes and the candidate's training data and run configuration
   are unchanged, continue the same attempt with:

   ```bash
   bash continue_eval.sh <same_attempt_id>
   ```

   This reuses the existing checkpoint and evaluates only the remaining tasks.
   Do not copy the candidate to a new attempt ID and do not call `run_attempt.sh`
   again for an unchanged candidate.
5. Use `bash run_attempt.sh <new_attempt_id>` again only when the data, run
   configuration, or intended checkpoint changes. Omit `--task-ids-file` only
   when training a new candidate directly for a full evaluation.
6. Keep one foreground attempt at a time per worker. Never use `nohup`, `&`, or another background wrapper with either `run_attempt.sh` or `continue_eval.sh`. RSIBench-Data owner reservations allow different workers to run concurrently while rejecting duplicate active attempts from the same worker.
7. Preserve evidence: score summaries, failure notes, validation artifacts, and candidate metadata.

Do not accept a candidate merely because it is format-valid or easy to generate.
Validate the actual training dataset for dense, independently verified behavioral
supervision, and reject data that overlaps current evaluation IDs, depends on
fabricated source material or observations, or repeats superficial templates.

When defining the diagnostic subset:

- Choose tasks because they represent important failure modes, not because they are easy to rerun.
- Include enough variety to cover the main bottlenecks you are trying to move.
- Keep the subset stable across nearby candidate comparisons unless the failure map itself changes.
- Write down in `DATA_PLAN.md` why each task or cluster is included and what change pattern would count as a promising screen.
- For a diagnostic subset, write the exact Harbor task names as a non-empty JSON list in `attempts/<attempt_id>/eval_task_ids.json`, then pass that file to the standard `run_attempt.sh` command with `--task-ids-file`. Record the parent attempt, solved/unsolved/representative rationale, and pass condition in `DATA_PLAN.md`.

When a diagnostic screen passes, run `bash continue_eval.sh <same_attempt_id>` to
complete the configured evaluation using the exact checkpoint already screened.
The script evaluates the task-ID complement, merges the task-level evidence, and
makes the same attempt final-selectable. Treat the subset score alone as
diagnostic evidence only.

Start a new full attempt with `bash run_attempt.sh <new_attempt_id>` and no
`--task-ids-file` only when evaluating a materially new candidate or checkpoint,
not when merely promoting an unchanged subset-tested checkpoint.

## Stage 4 — Review before iterating

After a completed, interpretable attempt:

1. Use `/attempt-review` or read `.claude/skills/attempt-review/SKILL.md`.
2. Update `ATTEMPT_REVIEW.md` with:
   - measured outcome
   - what changed
   - what did not change
   - failure-mode attribution
   - risks / side effects
   - decision: promote / revise / reject
   - next action
3. Append a structured entry to `CANDIDATES.jsonl`.
4. Only then decide whether to scale, revise, or submit.

### Decision rule

A candidate should advance because the evidence suggests it repaired an important failure mode, not because it looks more sophisticated or produced a superficially higher score once.

When you decide to stop exploring after an attempt, reread the shared
`.claude/leaderboard.json` and write a terminal recommendation to
`.claude/final_recommendations/<agent_id>.json`. Reference a completed full-set
attempt and its score in the JSON. This only tells the manager that this worker
is finished; do not run `final_submit.sh`. If you can still make progress, do
not write the recommendation and continue the loop.

## Budget discipline

- Preserve enough wall time and cost budget for final submission.
- Do not spend repeated full evaluations on candidates whose intermediate evidence is flat, ambiguous, or weakly validated.
- Prefer bounded diagnostic work between full evals when the failure map is already known.
- Stop speculative iteration when remaining budget cannot safely cover another candidate plus review and final submission.

## Minimal ledger discipline

Each completed candidate should leave behind:

- a candidate hypothesis in `DATA_PLAN.md`
- a validation note or artifact
- an attempt review entry in `ATTEMPT_REVIEW.md`
- one appended record in `CANDIDATES.jsonl`

If the written evidence is missing, the loop is incomplete.
