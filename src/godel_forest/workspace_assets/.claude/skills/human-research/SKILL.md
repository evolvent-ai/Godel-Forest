---
name: human-research
description: Human-style research workflow for understanding a benchmark, evaluation failures, and training dynamics before candidate design. Use it to inspect evidence and write a concise research note, not to design a data recipe.
---

# Human Research

Use this skill before designing a new candidate. Its purpose is to study the
benchmark, the model's evaluation behavior, and the training process as a human
researcher would. Collect evidence, compare explanations, and record uncertainty.
Leave the design of corresponding training data to `candidate-reflection`.

## Objective

Write a `Human research note` in `DATA_PLAN.md` that answers:

1. What does the benchmark actually reward at evaluation time?
2. Which failure modes are dominant for the current model?
3. What do the training process and training metrics reveal?
4. Which conclusions are supported, and what remains uncertain?

## Required inputs

Read local evidence first. Inspect:

- benchmark specifications, task format, parser, verifier, and environment rules
- aggregate evaluation distribution and task-family coverage
- overall and per-task evaluation results
- representative solved, failed, timeout, and error trajectories
- training logs, loss curves, checkpoints, and other available training metrics
- previous attempt reviews and research notes

Use external research only when local materials cannot answer an important
question.

### Required evaluation-distribution research

As a principle, generated training data must align with the aggregate distribution
of the evaluation dataset.

Use aggregate evaluation-distribution metadata as a primary research input when it
is available, including:

- repository or source distribution
- language, framework, package, or test-runner distribution
- coarse task families
- concentrated and long-tail portions of the evaluation set

Use this information only at the distribution level. Do not copy evaluation
questions, solutions, patches, hints, expected outputs, or other task-level answer
information.

### Evaluation-ID boundary

Identify the benchmark's canonical task or item ID field and the canonical IDs in
the current evaluation set. Research candidate real-data sources with enough
provenance to recover or normalize their canonical task or item IDs. The training
IDs and current evaluation IDs must have an empty intersection. This canonical-ID
overlap check is the contamination criterion for this supplemental workflow; do
not introduce additional contamination criteria.

## Research framing

Treat the benchmark as a structured behavior task, not merely a topic. Study:

- environment interaction versus static completion
- tool and answer protocol stability
- reasoning, localization, editing, verification, and recovery behavior
- distribution coverage and task difficulty
- trajectory form and stopping behavior

## Evidence-backed supervision quality prior

- Trajectories produced through real execution are high-quality trajectories.
- Long trajectories containing multiple substantive decision stages are high-quality trajectories.
- Trajectories close to the evaluation dataset in distribution, task form, environment, tool protocol, and interaction pattern are high-quality trajectories.

## Required output in `DATA_PLAN.md`

```md
## Human research note

### Evaluation target
...

### Evaluation findings
- ...

### Dominant failure modes
- ...

### Training findings
- Loss behavior: ...
- Other training metrics: ...
- Checkpoint / training-stage observations: ...

### Real-data findings
- Authentic sources available: ...
- Natural task structure, difficulty, edge cases, and outcome signals: ...
- Canonical task or item ID availability: ...

### Evaluation-ID overlap audit
- Current evaluation canonical ID source: ...
- Candidate-source canonical ID strategy: ...
- Required intersection: empty

### Supported conclusions
- ...

### Remaining uncertainty
- ...
```
