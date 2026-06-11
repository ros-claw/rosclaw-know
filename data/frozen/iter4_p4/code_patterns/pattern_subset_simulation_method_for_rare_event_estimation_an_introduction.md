---
pattern_id: pattern_subset_simulation_method_for_rare_event_estimation_an_introduction
schema_version: "2.0"
applicable_symptoms: [subset_simulation_method_for_rare_event_estimation_an_introduction]
domain: World_Physics
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Estimating small failure probabilities (e.g., 10^-6) via standard Monte Carlo requires prohibitively many samples.

**Domain**: `World_Physics`

## Symptom

Estimating small failure probabilities (e.g., 10^-6) via standard Monte Carlo requires prohibitively many samples.

## Diagnosis

Subset Simulation: decompose rare event into nested intermediate events, use Markov chain Monte Carlo to sample conditional levels, and compute overall probability as product of conditional probabilities.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Subset Simulation: decompose rare event into nested intermediate events, use Markov chain Monte Carlo to sample conditional levels, and compute overall probability as product of conditional probabilities.

## Code Target

_(no code target documented in source)_

## Fix

Subset Simulation: decompose rare event into nested intermediate events, use Markov chain Monte Carlo to sample conditional levels, and compute overall probability as product of conditional probabilities.

## Patch Sketch

```diff
--- subset_simulation_method_for_rare_event_estimation_an_introduction.before.py
+++ subset_simulation_method_for_rare_event_estimation_an_introduction.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Estimating small failure probabilities (e.g., 10^-6) via standard Monte Carlo requires prohibitively many samples.

+# Fix    : Subset Simulation: decompose rare event into nested intermediate events, use Markov chain Monte Carlo to sample conditional levels, and compute overall probability as product of conditional probabilities.

+# Avoid  : Standard Monte Carlo simulation with brute-force sampling of the rare event.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Standard Monte Carlo simulation with brute-force sampling of the rare event.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Memory_Reasoning** → Use chain-of-thought decomposition to break rare-event simulation into sequential conditional probabilities.
  - related fix: Use chain-of-thought prompting to decompose long instructions into step-by-step reasoning before action
- **Perception_Vision** → Use rare-event trajectories from importance sampling as ground-truth references to guide Monte Carlo sampling.
  - related fix: Use SLAM-derived trajectories as ground-truth motion tendency references to supervise or condition video world model predictions.
- **Control_Locomotion** → Use reinforcement learning to adaptively sample rare failure events via learned importance distributions.
  - related fix: Use reinforcement learning to learn a control policy that directly maps sensor observations to actuator commands for plasma shape and position control.

