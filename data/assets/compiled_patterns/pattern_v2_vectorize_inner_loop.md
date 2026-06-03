---
pattern_id: compiled_vectorize_inner_loop
schema_version: "2.0"
domain: Systems_Compute
task_families: ['unknown_optimization']
embodiment_types: []
artifact_languages: ['python']
priority: 0
source_quality: A
source_ids:
  - experiment1__openevolve__claude-opus-4.6__Astrodynamics_MannedLunarLanding
  - experiment1__openevolve__claude-opus-4.6__PyPortfolioOpt_robust_mvo_rebalance
  - experiment1__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment1__openevolve__deepseek-v3.2__Astrodynamics_MannedLunarLanding
  - experiment1__openevolve__deepseek-v3.2__Optics_fiber_wdm_channel_power_allocation
  - experiment1__openevolve__deepseek-v3.2__PyPortfolioOpt_robust_mvo_rebalance
  - experiment1__openevolve__gemini-3.1-pro-preview__PyPortfolioOpt_robust_mvo_rebalance
  - experiment1__openevolve__glm-5__Optics_fiber_mcs_power_scheduling
  # …and 37 more (truncated)
evidence:
  n: 45
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Top-scoring runs replace explicit Python iteration over candidate arrays with numpy / array-form operations

## Symptom

Top-scoring runs replace explicit Python iteration over candidate arrays with numpy / array-form operations.

## Diagnosis

Top-scoring runs replace explicit Python iteration over candidate arrays with numpy / array-form operations.  The inner-loop call overhead dominates wall-clock budget on score-bounded benchmarks.

## Preconditions

- The candidate artifact compiles and runs without errors on the baseline evaluator.

## Next Experiment

- Replace the inner Python loop with numpy / array operations: replaced explicit Python loop with numpy array form.

## Code Target

The whole editable artifact is the patch site.

## Patch Sketch

```python
# Replace explicit `for` over candidates with numpy array form.
x_vec = np.asarray(x_list)
scores = f_vectorised(x_vec)   # one call, vectorised body
```

## Expected Verifier Signal

- more candidates evaluated within the same time budget; score plateau pushed further.

## Anti-pattern

- Do not embed concrete tuning values from another task's baseline archive — see plan §3.5.

## Contraindications

- do not vectorise if the inner step has data-dependent branches — branchless numpy ops will compute discarded work and may even regress.
