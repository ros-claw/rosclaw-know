---
pattern_id: pattern_a_poisson_multi-bernoulli_mixture_filter_for_coexisting_point_and_extended_targe
schema_version: "2.0"
applicable_symptoms: [a_poisson_multi-bernoulli_mixture_filter_for_coexisting_point_and_extended_targe]
domain: Perception_Vision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Multi-target tracking fails when both point and extended targets coexist, as standard filters assume only one type.

**Domain**: `Perception_Vision`

## Symptom

Multi-target tracking fails when both point and extended targets coexist, as standard filters assume only one type.

## Diagnosis

Use a Poisson multi-Bernoulli mixture (PMBM) filter that propagates Gaussian densities for point targets and gamma Gaussian inverse Wishart densities for extended targets, with a generalized measurement model handling both types.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use a Poisson multi-Bernoulli mixture (PMBM) filter that propagates Gaussian densities for point targets and gamma Gaussian inverse Wishart densities for extended targets, with a generalized measurement model handling both types.

## Code Target

_(no code target documented in source)_

## Fix

Use a Poisson multi-Bernoulli mixture (PMBM) filter that propagates Gaussian densities for point targets and gamma Gaussian inverse Wishart densities for extended targets, with a generalized measurement model handling both types.

## Patch Sketch

```diff
--- a_poisson_multi-bernoulli_mixture_filter_for_coexisting_point_and_extended_targe.before.py
+++ a_poisson_multi-bernoulli_mixture_filter_for_coexisting_point_and_extended_targe.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Multi-target tracking fails when both point and extended targets coexist, as standard filters assume only one type.

+# Fix    : Use a Poisson multi-Bernoulli mixture (PMBM) filter that propagates Gaussian densities for point targets and gamma Gaussian inverse Wishart densities for extended targets, with a generalized measurement model handling both types.

+# Avoid  : Using separate filters for point and extended targets without a unified recursion leads to degraded performance in mixed scenarios.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Using separate filters for point and extended targets without a unified recursion leads to degraded performance in mixed scenarios.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Planning_Decision** → Hierarchical decomposition: rapid classification separates point and extended targets, then specialized filters handle each type.
  - related fix: Two-phase framework: rapid exploration builds symbolic scene graphs, then neurosymbolic planner reuses cached task-location trajectories for efficient deployment.
- **Control_Locomotion** → Train a unified neural tracker that maps raw sensor data directly to multi-type target states, bypassing hand-crafted filter assumptions.
  - related fix: Train a single neural network policy via deep reinforcement learning that maps raw depth camera images directly to motor commands, bypassing hand-crafted perception and control layers.
- **Memory_Reasoning** → Hierarchical decomposition of targets into point and extended layers with online type assignment.
  - related fix: Build hierarchical scene graph incrementally from semantic object map, with layers for objects, regions, rooms, and functional zones, updated online as new observations arrive.

