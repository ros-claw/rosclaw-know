---
pattern_id: pattern_graph_neural_net_using_analytical_graph_filters_and_topology_optimization_for_im
schema_version: "2.0"
applicable_symptoms: [graph_neural_net_using_analytical_graph_filters_and_topology_optimization_for_im]
domain: Perception_Vision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# CNN-based image denoising fails to generalize when training and testing data have different statistics, causing PSNR drop.

**Domain**: `Perception_Vision`

## Symptom

CNN-based image denoising fails to generalize when training and testing data have different statistics, causing PSNR drop.

## Diagnosis

Use a layered graph neural net with analytically defined GraphBio filters (no training) and optimize only the graph topology per layer via data training.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use a layered graph neural net with analytically defined GraphBio filters (no training) and optimize only the graph topology per layer via data training.

## Code Target

_(no code target documented in source)_

## Fix

Use a layered graph neural net with analytically defined GraphBio filters (no training) and optimize only the graph topology per layer via data training.

## Patch Sketch

```diff
--- graph_neural_net_using_analytical_graph_filters_and_topology_optimization_for_im.before.py
+++ graph_neural_net_using_analytical_graph_filters_and_topology_optimization_for_im.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: CNN-based image denoising fails to generalize when training and testing data have different statistics, causing PSNR drop.

+# Fix    : Use a layered graph neural net with analytically defined GraphBio filters (no training) and optimize only the graph topology per layer via data training.

+# Avoid  : Purely data-driven CNN filter coefficients that are not explainable and overfit to training data statistics.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Purely data-driven CNN filter coefficients that are not explainable and overfit to training data statistics.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Planning_Decision** → Hierarchical decomposition: first estimate noise statistics, then apply specialized denoising conditioned on that estimate.
  - related fix: Disentangled reasoning via Chain-of-Thought: first predict a landmark-based plan (high-level), then execute low-level actions conditioned on that plan.
- **Learning_Training** → Use adaptive multi-kernel filtering to combine data-driven and prior-based denoising paths.
  - related fix: Cross-space adaptive filter (CSF) combining a topology-based low-pass filter (Mercer kernel) and an attribute-based high-pass filter (derived from kernel ridge regression) via multiple-kernel learning.
- **Learning_Training** → Use synthetic data generation from structured priors to match target noise statistics.
  - related fix: Use Marky to programmatically generate 4.2 million synthetic instruction–trajectory pairs from structured environment representations and action sequences.

