---
pattern_id: compiled_swap_random_search_to_structured_optimizer
schema_version: "2.0"
domain: Control_Locomotion
task_families: ['robotics_optimization']
embodiment_types: ['uav', 'manipulator', 'wheeled_robot', 'quadruped']
artifact_languages: ['python', 'cpp']
priority: 0
source_quality: A
source_ids:
  - experiment1__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__abmcts__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__shinkaevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment2__shinkaevolve__gpt-oss-120b__Robotics_PIDTuning
evidence:
  n: 5
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Random search saturates well below the achievable score on PID-tuning tasks within the iteration budget

## Symptom

Random search saturates well below the achievable score on PID-tuning tasks within the iteration budget.

## Diagnosis

Random search saturates well below the achievable score on PID-tuning tasks within the iteration budget.  Top runs replace it with a structured optimizer (CMA-ES, Bayesian, differential evolution).

## Preconditions

- The candidate artifact compiles and runs without errors on the baseline evaluator.

## Next Experiment

- Swap the search loop to a structured optimiser: swapped optimizer from random search to CMA-ES-style strategy.

## Code Target

Locate the main search loop (typically a function whose name contains `optimize` / `search` / `solve`) and treat its body as the patch site.

## Patch Sketch

```python
# Replace `random_search()` with a structured optimiser.
# Acceptable choices: CMA-ES, Bayesian, differential evolution.
# Pair with a wall-clock deadline (see add_time_budget).
```

## Expected Verifier Signal

- score climbs monotonically past the random-search plateau before the time budget elapses.

## Anti-pattern

- Do not embed concrete tuning values from another task's baseline archive — see plan §3.5.

## Contraindications

- do not run unbounded — pair with an explicit time budget so the search returns before the wall clock expires (see candidate_add_time_budget)
