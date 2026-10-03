---
name: diagnostic-eval-plan
description: Plan evaluation after training. Check fundamental model capability first, then run a small representative subset from the evaluation set, and complete the full evaluation only if the candidate passes.
---

# Diagnostic Evaluation Plan

Use this skill after a candidate model has been trained. Evaluate in three stages
so that obviously unusable candidates do not consume the full evaluation budget.

## 1. Check fundamental capability

First run the smallest representative check needed to confirm that the model can
participate in the benchmark at all.

For tool-using or environment-interaction tasks, verify that the model can:

- produce valid tool calls accepted by the harness
- use tool observations in later decisions
- complete a basic inspect, act, observe, and submit loop

For non-interactive tasks, verify that the model can:

- follow the required answer format
- produce parseable, relevant answers
- perform coherent reasoning appropriate to the task

If fundamental capability is systematically absent, stop. First distinguish model
failure from checkpoint, prompt, parser, proxy, or evaluation-infrastructure
failure. If the candidate itself is responsible, reconsider the overall data
generation and training strategy instead of spending budget on further evaluation.

## 2. Run a small evaluation subset

If the capability check passes, select a small representative subset directly from
the evaluation set. The subset should provide useful evidence about the candidate,
not merely contain the easiest available tasks.

When prior evaluation evidence exists, include:

- tasks representing the failure modes the candidate is intended to improve
- some previously solved or basic tasks to detect regressions
- representative task families or domains from the evaluation distribution

Exclude known infrastructure failures or unusably noisy tasks. Use the same
checkpoint, prompt, agent configuration, answer protocol, and scoring rules that
will be used for the full evaluation.

Before running the subset, state what result is sufficient to continue. Continue
only if the model retains fundamental capability, shows adequate task performance,
and does not exhibit serious regressions or systematic protocol failures.

Record the selected subset as a non-empty JSON list of exact Harbor task names in
`../../attempts/<attempt_id>/eval_task_ids.json`:

```json
["django__django-123", "sympy__sympy-456"]
```

When a parent attempt exists, include both previously solved and previously
unsolved tasks. Record the parent, each selection bucket and rationale, and the
pass condition in `../../DATA_PLAN.md`.

Run the standard RSIBench-Data attempt entrypoint in the foreground:

```bash
bash ../../run_attempt.sh <attempt_id> \
  --task-ids-file attempts/<attempt_id>/eval_task_ids.json
```

The option path is resolved relative to the main RSIBench-Data workspace. Do not
use `nohup`, `&`, or a detached shell. A subset score is diagnostic evidence and
must not be treated as a full-evaluation score. The standard command reserves
this worker's owner slot and planned training cost before training begins.

## 3. Complete the full evaluation

If the subset passes and the candidate's training data and run configuration are
unchanged, continue the same attempt with its existing checkpoint. Do not copy
the data into a new attempt ID and do not invoke `run_attempt.sh` again. From the
main workspace use:

```bash
bash continue_eval.sh <same_attempt_id>
```

From a parallel worker scratch workspace use:

```bash
bash ../../continue_eval.sh <same_attempt_id>
```

`continue_eval.sh` evaluates only the configured tasks not already covered by
the subset, merges task-level rewards and sampling cost, and upgrades the same
attempt from `evaluation_scope=subset` to `evaluation_scope=full`. It reuses the
original `model_path` and does not train again. Run it in the foreground without
`nohup`, `&`, or a detached shell.

Use `run_attempt.sh <new_attempt_id>` again only if the training data or run
configuration changed, or if you intentionally want a different checkpoint.
For a new candidate evaluated directly on the full set, omit `--task-ids-file`.
Report the completed full-coverage result rather than treating the small subset
as the final score.

If the subset fails, stop and analyze the failures before spending budget on the
remaining instances. Use `attempt-review` to identify the unresolved failure
clusters and reconsider the candidate or data strategy.

## Required evaluation plan

Record a short plan before evaluation:

```md
## Evaluation plan

### Fundamental capability check
- Tasks / checks: ...
- Pass condition: ...

### Small evaluation subset
- Selection rationale: ...
- Included task families / failure modes: ...
- Harbor task names: ...
- Pass condition: ...

### Full evaluation
- Full configured evaluation coverage: ...
- Continuation command for unchanged candidate: `bash continue_eval.sh <same_attempt_id>`
- New attempt ID only if data/config/checkpoint changes: ...
```
