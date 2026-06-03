---
pattern_id: compiled_generic_time_budget
schema_version: "2.0"
domain: World_Physics
task_families: ['optics_optimization']
embodiment_types: ['optical_system']
artifact_languages: ['python']
priority: 0
source_quality: A
source_ids:
  - experiment1__openevolve__claude-opus-4.6__Optics_fiber_guardband_spectrum_packing
  - experiment1__openevolve__glm-5__Optics_holographic_multiplane_focusing
  - experiment2__abmcts__claude-opus-4.6__Aerodynamics_CarAerodynamicsSensing
  - experiment2__abmcts__gpt-oss-120b__Optics_phase_dammann_uniform_orders
  - experiment2__abmcts__gpt-oss-120b__StructuralOptimization_ISCSO2023
  - experiment2__openevolve__claude-opus-4.6__Optics_fiber_guardband_spectrum_packing
  - experiment2__openevolve__gpt-oss-120b__Optics_fiber_guardband_spectrum_packing
  - experiment2__shinkaevolve__claude-opus-4.6__Optics_fiber_guardband_spectrum_packing
evidence:
  n: 8
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Structured optimisers can overrun the per-task wall clock if left ungated

## Symptom

Structured optimisers can overrun the per-task wall clock if left ungated.

## Diagnosis

Structured optimisers can overrun the per-task wall clock if left ungated.  Top entries explicitly check ``time.time() < deadline`` inside their search loop.

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

- evaluator returncode stays 0; no wall-clock-timeout infeasibility.

## Anti-pattern

- Do not embed concrete tuning values from another task's baseline archive — see plan §3.5.

## Contraindications

- _(no known contraindications)_

## Cross-domain analogy

**Systems_Compute**: same shape as a request timeout in an RPC client — gate the inner loop on a wall-clock deadline, fail soft when it elapses.
