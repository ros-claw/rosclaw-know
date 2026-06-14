---
pattern_id: pattern_performance_of_genetic_algorithms_in_the_context_of_software_model_refactoring
schema_version: "2.0"
applicable_symptoms: [performance_of_genetic_algorithms_in_the_context_of_software_model_refactoring]
domain: Planning_Decision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Large search space of software model refactoring actions makes it hard to find good alternatives efficiently.

**Domain**: `Planning_Decision`

## Symptom

Large search space of software model refactoring actions makes it hard to find good alternatives efficiently.

## Diagnosis

Use multi-objective genetic algorithms (e.g., NSGA-II, PESA2) to explore refactoring alternatives by optimizing multiple objectives simultaneously.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use multi-objective genetic algorithms (e.g., NSGA-II, PESA2) to explore refactoring alternatives by optimizing multiple objectives simultaneously.

## Code Target

_(no code target documented in source)_

## Fix

Use multi-objective genetic algorithms (e.g., NSGA-II, PESA2) to explore refactoring alternatives by optimizing multiple objectives simultaneously.

## Patch Sketch

```diff
--- performance_of_genetic_algorithms_in_the_context_of_software_model_refactoring.before.py
+++ performance_of_genetic_algorithms_in_the_context_of_software_model_refactoring.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Large search space of software model refactoring actions makes it hard to find good alternatives efficiently.

+# Fix    : Use multi-objective genetic algorithms (e.g., NSGA-II, PESA2) to explore refactoring alternatives by optimizing multiple objectives simultaneously.

+# Avoid  : Single-objective or exhaustive search approaches that cannot handle the combinatorial explosion of refactoring actions.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Single-objective or exhaustive search approaches that cannot handle the combinatorial explosion of refactoring actions.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Memory_Reasoning** → Decompose the refactoring search space into sequential sub-problems with guided reasoning steps.
  - related fix: Use chain-of-thought prompting to decompose long instructions into step-by-step reasoning before action
- **Systems_Compute** → Use dynamic search-space pruning by monitoring refactoring impact variance to adaptively focus exploration.
  - related fix: Use dynamic reconfiguration to automatically detect warmup end, e.g., by monitoring performance metric variance and adjusting the warmup period online.
- **Learning_Training** → Use causal graph intervention to prune irrelevant refactoring actions, reducing search space.
  - related fix: Use causal counterfactual reasoning to remove the influence of sensitive attributes on predictions by intervening on the causal graph.

