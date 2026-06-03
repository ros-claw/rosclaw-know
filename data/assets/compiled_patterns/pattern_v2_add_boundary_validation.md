---
pattern_id: compiled_add_boundary_validation
schema_version: "2.0"
domain: Systems_Compute
task_families: ['unknown_optimization']
embodiment_types: []
artifact_languages: ['python']
priority: 0
source_quality: A
source_ids:
  - experiment1__openevolve__claude-opus-4.6__Aerodynamics_CarAerodynamicsSensing
  - experiment1__openevolve__claude-opus-4.6__Astrodynamics_MannedLunarLanding
  - experiment1__openevolve__claude-opus-4.6__KernelEngineering_MLA
  - experiment1__openevolve__deepseek-v3.2__Aerodynamics_CarAerodynamicsSensing
  - experiment1__openevolve__deepseek-v3.2__Astrodynamics_MannedLunarLanding
  - experiment1__openevolve__deepseek-v3.2__KernelEngineering_MLA
  - experiment1__openevolve__gemini-3.1-pro-preview__Aerodynamics_CarAerodynamicsSensing
  - experiment1__openevolve__gemini-3.1-pro-preview__Astrodynamics_MannedLunarLanding
  # …and 33 more (truncated)
evidence:
  n: 41
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Successful runs insert finiteness / range checks at the boundary of their solver so a NaN or invalid output is caught before the evaluator returns 'infeasible'

## Symptom

Successful runs insert finiteness / range checks at the boundary of their solver so a NaN or invalid output is caught before the evaluator returns 'infeasible'.

## Diagnosis

Successful runs insert finiteness / range checks at the boundary of their solver so a NaN or invalid output is caught before the evaluator returns 'infeasible'.  Catches a large fraction of soft failures in Frontier-Eng's pure-Python tasks.

## Preconditions

- The candidate artifact compiles and runs without errors on the baseline evaluator.

## Next Experiment

- Add a finiteness / range check at the boundary: added boundary / finiteness check on intermediate value.

## Code Target

The whole editable artifact is the patch site.

## Patch Sketch

```python
# Finiteness + range guard at the function boundary.
if not np.all(np.isfinite(out)):
    raise ValueError("non-finite output")
out = np.clip(out, lo, hi)
```

## Expected Verifier Signal

- feasibility rate stays high even on novel candidate configurations.

## Anti-pattern

- Do not embed concrete tuning values from another task's baseline archive — see plan §3.5.

## Contraindications

- _(no known contraindications)_
