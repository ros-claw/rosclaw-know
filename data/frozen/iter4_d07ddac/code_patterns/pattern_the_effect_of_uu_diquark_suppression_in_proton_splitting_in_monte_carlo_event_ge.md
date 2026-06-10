---
pattern_id: pattern_the_effect_of_uu_diquark_suppression_in_proton_splitting_in_monte_carlo_event_ge
schema_version: "2.0"
applicable_symptoms: [the_effect_of_uu_diquark_suppression_in_proton_splitting_in_monte_carlo_event_ge]
domain: World_Physics
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Proton splitting in Monte Carlo event generators assumes (uu)-diquark probability 1/3, but fails to describe NA49 p+p→p+X data at 158 GeV/c.

**Domain**: `World_Physics`

## Symptom

Proton splitting in Monte Carlo event generators assumes (uu)-diquark probability 1/3, but fails to describe NA49 p+p→p+X data at 158 GeV/c.

## Diagnosis

Set (uu)-diquark suppression probability to 1/6 in the Fritiof (FTF) model of Geant4.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Set (uu)-diquark suppression probability to 1/6 in the Fritiof (FTF) model of Geant4.

## Code Target

_(no code target documented in source)_

## Fix

Set (uu)-diquark suppression probability to 1/6 in the Fritiof (FTF) model of Geant4.

## Patch Sketch

```diff
--- the_effect_of_uu_diquark_suppression_in_proton_splitting_in_monte_carlo_event_ge.before.py
+++ the_effect_of_uu_diquark_suppression_in_proton_splitting_in_monte_carlo_event_ge.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Proton splitting in Monte Carlo event generators assumes (uu)-diquark probability 1/3, but fails to describe NA49 p+p→p+X data at 158 GeV/c.

+# Fix    : Set (uu)-diquark suppression probability to 1/6 in the Fritiof (FTF) model of Geant4.

+# Avoid  : Using default (uu)-diquark probability of 1/3.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Using default (uu)-diquark probability of 1/3.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Learning_Training** → Use closed-loop verification to filter generated diquark splits, iteratively retuning splitting probabilities against data.
  - related fix: Self-Refining Data Flywheel: generate synthetic navigation trajectories via a teacher policy, filter with a learned verifier, and iteratively retrain the student policy on the augmented data.

