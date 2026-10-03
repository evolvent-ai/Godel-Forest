---
name: attempt-review
description: Concise post-evaluation review workflow. Use after an attempt completes to separate infrastructure noise from model behavior, analyze the evaluation tasks the model did not solve, and record the resulting failure clusters in ATTEMPT_REVIEW.md and CANDIDATES.jsonl.
---

# Attempt Review

Use this skill after a completed, interpretable evaluation. The review must explain
what changed, which evaluation problems remain unsolved, what capabilities they
require, and which failure clusters should be considered next.

## Required evidence

Inspect:

- score, per-task outcomes, regressions, and evaluation errors
- representative solved, failed, timeout, and newly changed trajectories
- `DATA_PLAN.md`, prior reviews, and candidate metadata

## Workflow

### 1. Record measured facts

Write the score change, completed/error counts, regressions, and whether the
attempt completed cleanly. Separate infrastructure failures from model failures.

### 2. Attribute behavioral changes

Identify which behaviors improved or worsened, for example:

- valid protocol and stopping behavior
- task localization and evidence use
- edit correctness and verification
- recovery from failed commands or hypotheses
- action efficiency, context growth, and timeouts
- coverage across evaluation slices

Do not treat a score increase alone as proof that the data strategy is correct.

### 3. Analyze unsolved evaluation tasks

Analyze evaluation tasks the model did not solve. Exclude infrastructure-only
failures, then group genuine model failures by repository or domain, task family,
failure stage, required capability, and trajectory pattern. Inspect representative
trajectories to identify where the model first went wrong, such as:

- misunderstanding the task or required output protocol
- failing to inspect, localize, or gather sufficient evidence
- choosing an incorrect hypothesis, tool sequence, edit, or reasoning path
- failing to recover after contradictory evidence or command failure
- weak verification, premature submission, context blowup, or timeout
- lacking domain, repository, environment, or task-family familiarity

Prioritize repeated or high-impact failure clusters rather than isolated noisy
tasks. For each cluster, record:

- the missing capability or behavior
- the evaluation evidence supporting the diagnosis
- its frequency, impact, or priority
- where the trajectory first diverged from an effective approach

Use evaluation tasks only to infer aggregate distributions and capability gaps. Do
not copy evaluation questions, solutions, patches, or task-specific hints into
downstream planning.

### 4. Decide the next action

- `promote`: the intended failure mode improved without major new brittleness
- `revise`: useful signal exists, but important failure clusters remain unresolved
- `reject`: the direction does not repair the bottleneck or introduces harmful behavior

Update `CANDIDATES.jsonl` with the attempt result, decision, reason, and next action.

## Required `ATTEMPT_REVIEW.md` format

```md
## Attempt <id>

### Measured outcome
- ...

### Behavioral attribution
- Improved: ...
- Unchanged: ...
- Regressed: ...
- Infrastructure-only failures: ...

### Unsolved evaluation analysis
| Failure cluster | Evaluation evidence | Missing capability | Priority |
|---|---|---|---|
| ... | ... | ... | ... |

### Decision
- Promote / Revise / Reject

### Next action
- ...
```

If the review cannot connect an unsolved task to a defensible capability gap, say
that the evidence is insufficient rather than inventing an attribution.
