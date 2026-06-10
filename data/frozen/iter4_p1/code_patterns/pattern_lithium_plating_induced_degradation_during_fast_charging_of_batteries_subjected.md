---
pattern_id: pattern_lithium_plating_induced_degradation_during_fast_charging_of_batteries_subjected
schema_version: "2.0"
applicable_symptoms: [lithium_plating_induced_degradation_during_fast_charging_of_batteries_subjected_]
domain: Systems_Compute
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Fast charging at 4C under compressive loading (0-440 kPa) causes significant capacity fade and active lithium loss in lithium-ion pouch cells.

**Domain**: `Systems_Compute`

## Symptom

Fast charging at 4C under compressive loading (0-440 kPa) causes significant capacity fade and active lithium loss in lithium-ion pouch cells.

## Diagnosis

Reduce compressive load or limit charging rate to 1C to mitigate lithium plating and capacity degradation.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Reduce compressive load or limit charging rate to 1C to mitigate lithium plating and capacity degradation.

## Code Target

_(no code target documented in source)_

## Fix

Reduce compressive load or limit charging rate to 1C to mitigate lithium plating and capacity degradation.

## Patch Sketch

```diff
--- lithium_plating_induced_degradation_during_fast_charging_of_batteries_subjected_.before.py
+++ lithium_plating_induced_degradation_during_fast_charging_of_batteries_subjected_.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Fast charging at 4C under compressive loading (0-440 kPa) causes significant capacity fade and active lithium loss in lithium-ion pouch cells.

+# Fix    : Reduce compressive load or limit charging rate to 1C to mitigate lithium plating and capacity degradation.

+# Avoid  : Applying compressive loads during fast charging exacerbates lithium plating and capacity loss.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Applying compressive loads during fast charging exacerbates lithium plating and capacity loss.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Memory_Reasoning** → Decompose the fast-charging protocol into stepwise current steps with closed-loop voltage verification after each step.
  - related fix: Use chain-of-thought prompting to decompose long instructions into step-by-step reasoning before action

