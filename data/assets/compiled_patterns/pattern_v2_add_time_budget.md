---
pattern_id: compiled_add_time_budget
schema_version: "2.0"
domain: Control_Locomotion
task_families: ['robotics_optimization']
embodiment_types: ['uav', 'manipulator', 'wheeled_robot', 'quadruped']
artifact_languages: ['python', 'cpp']
priority: 0
source_quality: B
source_ids:
  - experiment1__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__abmcts__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__shinkaevolve__claude-opus-4.6__Robotics_PIDTuning
evidence:
  n: 4
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# A structured optimizer with no wall-clock guard can run past the evaluator timeout and lose the run entirely

## Symptom

A structured optimizer with no wall-clock guard can run past the evaluator timeout and lose the run entirely.

## Diagnosis

A structured optimizer with no wall-clock guard can run past the evaluator timeout and lose the run entirely.  Successful entries gate their search on ``time.time() < deadline``.

## Preconditions

- The candidate artifact compiles and runs without errors on the baseline evaluator.

## Next Experiment

- Gate the search loop on a wall-clock deadline: added wall-clock time budget guard.

## Code Target

Locate every `while` / `for` loop that controls the search iteration budget; treat its guard expression as the patch site.

## Patch Sketch

```python
import time
DEADLINE = time.time() + WALLCLOCK_BUDGET
while time.time() < DEADLINE:
    ...   # search step
```

## Expected Verifier Signal

- evaluator returncode stays 0 even when the search explores deep alternatives.

## Anti-pattern

- Do not embed concrete tuning values from another task's baseline archive — see plan §3.5.

## Contraindications

- _(no known contraindications)_

## Cross-domain analogy

**Systems_Compute**: same shape as a request timeout in an RPC client — gate the inner loop on a wall-clock deadline, fail soft when it elapses.
