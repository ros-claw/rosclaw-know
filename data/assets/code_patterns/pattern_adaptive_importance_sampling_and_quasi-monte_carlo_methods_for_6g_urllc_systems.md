---
pattern_id: pattern_adaptive_importance_sampling_and_quasi-monte_carlo_methods_for_6g_urllc_systems
schema_version: "2.0"
applicable_symptoms: [adaptive_importance_sampling_and_quasi-monte_carlo_methods_for_6g_urllc_systems]
domain: Systems_Compute
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Evaluating bit/word error rate for coded modulation in 6G URLLC requires high-dimensional black-box integration and rare-event sampling, where standard Monte Carlo is too slow.

**Domain**: `Systems_Compute`

## Symptom

Evaluating bit/word error rate for coded modulation in 6G URLLC requires high-dimensional black-box integration and rare-event sampling, where standard Monte Carlo is too slow.

## Diagnosis

Adaptive importance sampling that automatically finds the optimal Gaussian proposal from previous samples, combined with quasi-Monte Carlo integration.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Adaptive importance sampling that automatically finds the optimal Gaussian proposal from previous samples, combined with quasi-Monte Carlo integration.

## Code Target

_(no code target documented in source)_

## Fix

Adaptive importance sampling that automatically finds the optimal Gaussian proposal from previous samples, combined with quasi-Monte Carlo integration.

## Patch Sketch

```diff
--- adaptive_importance_sampling_and_quasi-monte_carlo_methods_for_6g_urllc_systems.before.py
+++ adaptive_importance_sampling_and_quasi-monte_carlo_methods_for_6g_urllc_systems.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Evaluating bit/word error rate for coded modulation in 6G URLLC requires high-dimensional black-box integration and rare-event sampling, where standard Monte Carlo is too slow.

+# Fix    : Adaptive importance sampling that automatically finds the optimal Gaussian proposal from previous samples, combined with quasi-Monte Carlo integration.

+# Avoid  : Standard Monte Carlo method

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Standard Monte Carlo method

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Control_Locomotion** → Use positive-weight geometric sampling to efficiently cover rare high-dimensional integration regions.
  - related fix: Geometric unscented sampling (GUS) selects uniformly distributed samples with positive weights based on probability and geometric location, combined with CKF's update framework.
- **Planning_Decision** → Learn a direct mapping from channel observations to error estimates via neural rare-event simulation.
  - related fix: End-to-end trajectory learning with Vision-Language-Exploration pre-training over a million diverse RGB-D trajectories, directly mapping raw sensor observations to continuous commands.
- **Control_Locomotion** → Use a lightweight surrogate model trained offline to approximate the rare-event integration.
  - related fix: Use a lightweight MLP or RNN policy trained via RL in simulation, executed at 50-100 Hz for direct joint-level torque/position commands.

