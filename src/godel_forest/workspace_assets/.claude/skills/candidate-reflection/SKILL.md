---
name: candidate-reflection
description: Mid-loop candidate design checkpoint. Use when proposing a new candidate or materially revising an old one. It turns research findings and review evidence into one explicit candidate hypothesis before data generation begins.
---

# Candidate Reflection

Use this skill after `human-research` and before generating or substantially revising a candidate. Its job is to translate evidence into exactly one candidate hypothesis.

## Objective

Write down a candidate proposal that is specific enough to test, but abstract enough to avoid cargo-culting a recipe. The output should explain:

- which failure cluster from `ATTEMPT_REVIEW.md` it targets
- what failure mode this candidate targets
- what corresponding training data should be added
- what supervision structure it changes
- what it intentionally does **not** change
- what validation must pass before launching the attempt
- why the proposed supervision is authentic, source-grounded, and verifiable

## Workflow

### 1. State the candidate hypothesis

Describe the proposal in terms of behavior, not just topic.

Good framing:

- "This candidate tests whether cleaner X behavior improves Y failure mode."
- "This candidate broadens coverage along Z dimension while holding protocol structure constant."
- "This candidate tightens validation without changing the core task distribution."

### 2. Translate the failure into a data proposal

Select one supported, high-priority failure cluster from `ATTEMPT_REVIEW.md`.
Design corresponding training data by specifying:

- the missing capability or behavior to teach
- the source, domain, or task-family characteristics needed for coverage
- the difficulty and trajectory characteristics needed to exercise that capability
- the successful behavior and independent verification signal the data must contain
- the expected evaluation behavior that should improve if the hypothesis is correct

Use evaluation evidence within the RSIBench-Data rules. For contamination control,
compare the canonical task or item IDs represented by the candidate data with the
canonical IDs in the current evaluation set; the intersection must be empty.

### 3. Bound the change

Name what stays fixed. A candidate is easier to interpret when it changes one main thing at a time.

Examples of bounded changes:

- change supervision structure while keeping source family similar
- change coverage breadth while keeping validation protocol fixed
- change termination or action behavior while keeping core task content fixed

### 4. Define candidate validation

Define validation for the candidate dataset that will actually be trained. This can include:

- schema / parser conformance checks
- executability checks
- answer verification checks
- dataset quality inspection
- contamination / artifact inspection

The contamination audit must record how canonical task or item IDs were obtained
or normalized for both the candidate data and the current evaluation set, and must
confirm that their intersection is empty. Canonical evaluation-ID overlap is the
contamination criterion for this supplemental workflow; do not introduce
additional contamination criteria.

For environment-interaction trajectories, also require a supervision-integrity
check:

- tool observations were produced by real execution
- edits are followed by an independent validation signal
- failures and retries are represented naturally when they occur
- stopping and submission behavior matches the evaluation harness

Do not confuse a schema-valid record with a behaviorally valid record.

Keep the candidate deliberately compact. Every retained example should contribute
distinct verified behavioral information. Remove examples that merely repeat the
same action template, differ only superficially, or add volume without teaching a
new decision, recovery behavior, environment condition, or failure mode.
Prefer tasks with genuine complexity that require multiple substantive
decision-making stages to solve. For environment-interaction tasks, favor examples
where those stages naturally form longer, information-dense trajectories through
exploration, evidence gathering, hypothesis revision, failure recovery, and final
validation. Length should come from the real solution process rather than
artificial step splitting or repeated actions. For non-interactive reasoning tasks,
require an equally complete, deep, and verifiable derivation without requiring
multiple interaction turns.

### 5. Define interpretation criteria

Write down what result pattern would support the hypothesis and what would undermine it.

## Required output in `DATA_PLAN.md`

Add a short section:

```md
### Candidate hypothesis
...

### Target failure cluster
...

### Corresponding data addition
- Missing capability: ...
- Source / domain / task-family characteristics: ...
- Difficulty / trajectory characteristics: ...
- Required successful behavior: ...
- Independent success signal: ...

### Intended change
...

### Held constant
...

### Candidate validation
- ...
- ...

### Real-data rationale
- Authentic source and preserved task structure: ...
- Natural difficulty, edge cases, and solution behavior: ...

### Evaluation-ID overlap audit
- Candidate canonical IDs: ...
- Current evaluation canonical IDs: ...
- Intersection: empty

### Supervision integrity
- Real execution evidence: ...
- Causal observation grounding: ...
- Independent outcome verification: ...
- Recovery / failure behavior: ...
- Protocol and stopping fidelity: ...

### Interpretation plan
- Support if ...
- Doubt if ...
```

## Avoid

- bundling many unrelated changes into one candidate
- changing source, trajectory structure, validation style, and optimizer all at once unless required by the benchmark contract
- treating a candidate as justified just because it sounds benchmark-relevant
- optimizing record count instead of verified behavioral information
- retaining redundant examples when a smaller, higher-quality dataset carries the
  same or better behavioral signal
- turning one rigid successful script into many superficially different examples
- launching an attempt before validating the actual dataset that will be trained
