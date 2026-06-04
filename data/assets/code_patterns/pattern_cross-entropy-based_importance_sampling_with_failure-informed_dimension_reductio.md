---
pattern_id: pattern_cross-entropy-based_importance_sampling_with_failure-informed_dimension_reductio
schema_version: "2.0"
applicable_symptoms: [cross-entropy-based_importance_sampling_with_failure-informed_dimension_reductio]
domain: World_Physics
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Rare event simulation in high dimensions (O(1e2) or more) suffers from poor efficiency when using cross-entropy importance sampling with standard parametric families.

**Domain**: `World_Physics`

## Symptom

Rare event simulation in high dimensions (O(1e2) or more) suffers from poor efficiency when using cross-entropy importance sampling with standard parametric families.

## Diagnosis

Exploit connection between rare event simulation and Bayesian inverse problems to identify intrinsic low-dimensional structure, then apply dimension reduction techniques (e.g., from [47]) to construct effectively low-dimensional biasing distributions within the cross-entropy method.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Exploit connection between rare event simulation and Bayesian inverse problems to identify intrinsic low-dimensional structure, then apply dimension reduction techniques (e.g., from [47]) to construct effectively low-dimensional biasing distributions within the cross-entropy method.

## Code Target

_(no code target documented in source)_

## Fix

Exploit connection between rare event simulation and Bayesian inverse problems to identify intrinsic low-dimensional structure, then apply dimension reduction techniques (e.g., from [47]) to construct effectively low-dimensional biasing distributions within the cross-entropy method.

## Patch Sketch

```diff
--- cross-entropy-based_importance_sampling_with_failure-informed_dimension_reductio.before.py
+++ cross-entropy-based_importance_sampling_with_failure-informed_dimension_reductio.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Rare event simulation in high dimensions (O(1e2) or more) suffers from poor efficiency when using cross-entropy importance sampling with standard parametric families.

+# Fix    : Exploit connection between rare event simulation and Bayesian inverse problems to identify intrinsic low-dimensional structure, then apply dimension reduction techniques (e.g., from [47]) to construct effectively low-dimensional biasing distributions within the cross-entropy method.

+# Avoid  : Directly applying cross-entropy method with existing parametric families in high dimensions (O(1e2) or more) without dimension reduction.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Directly applying cross-entropy method with existing parametric families in high dimensions (O(1e2) or more) without dimension reduction.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Learning_Training** → Decompose rare event sampling into specialized low-dimensional experts combined via adaptive weighting.
  - related fix: Train three specialized RL experts (reaching, squeezing, avoiding) and combine them via dynamic weighting based on individual capability performance.

