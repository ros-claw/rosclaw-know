---
pattern_id: compiled_warm_start_from_prior_best
schema_version: "2.0"
domain: Systems_Compute
task_families: ['unknown_optimization']
embodiment_types: []
artifact_languages: ['python']
priority: 0
source_quality: A
source_ids:
  - experiment1__openevolve__claude-opus-4.6__JobShop_ta
  - experiment1__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment1__openevolve__glm-5__Robotics_PIDTuning
  - experiment1__openevolve__gpt-5.4__InventoryOptimization_tree_gsm_safety_stock
  - experiment2__abmcts__claude-opus-4.6__JobShop_abz
  - experiment2__abmcts__claude-opus-4.6__JobShop_ta
  - experiment2__abmcts__gpt-oss-120b__InventoryOptimization_tree_gsm_safety_stock
  - experiment2__openevolve__claude-opus-4.6__JobShop_ta
  # …and 8 more (truncated)
evidence:
  n: 16
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Top entries seed their optimiser from a prior-best solution (their own or a sibling task's) rather than starting from a random or hand-tuned baseline

## Symptom

Top entries seed their optimiser from a prior-best solution (their own or a sibling task's) rather than starting from a random or hand-tuned baseline.

## Diagnosis

Top entries seed their optimiser from a prior-best solution (their own or a sibling task's) rather than starting from a random or hand-tuned baseline.  Cuts the path-length to a high score significantly.

## Preconditions

- The candidate artifact compiles and runs without errors on the baseline evaluator.

## Next Experiment

- Seed the optimiser from a prior-best solution: seeded optimizer from a prior-best solution.

## Code Target

The whole editable artifact is the patch site.

## Patch Sketch

```python
# Warm-start from a prior-best solution on the SAME task.
# DO NOT embed concrete tuning values from another task's archive.
initial = load_prior_best_for_this_task()
optimiser = make_optimizer(initial=initial)
```

## Expected Verifier Signal

- first valid score is already close to the search ceiling; subsequent iterations refine rather than discover.

## Anti-pattern

- Do not embed concrete tuning values from another task's baseline archive — see plan §3.5.

## Contraindications

- never embed *concrete* gain values from another task verbatim — that turns the pattern into a leaderboard cheat sheet (see plan §3.5).
- the seed must come from a previous run on the *same* task; cross-task seeding is much less reliable.
