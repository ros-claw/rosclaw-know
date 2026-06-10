---
pattern_id: pattern_capacity_fade_due_to_side-reactions_in_silicon_anodes_in_lithium-ion_batteries
schema_version: "2.0"
applicable_symptoms: [capacity_fade_due_to_side-reactions_in_silicon_anodes_in_lithium-ion_batteries]
domain: World_Physics
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Capacity fade in silicon anodes due to continuous electrolyte-reduction side-reactions on freshly-exposed electrode surfaces during cycling

**Domain**: `World_Physics`

## Symptom

Capacity fade in silicon anodes due to continuous electrolyte-reduction side-reactions on freshly-exposed electrode surfaces during cycling

## Diagnosis

Use electrolyte formulation EC:DEC with FEC additive to minimize coulombic losses from side-reactions

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use electrolyte formulation EC:DEC with FEC additive to minimize coulombic losses from side-reactions

## Code Target

_(no code target documented in source)_

## Fix

Use electrolyte formulation EC:DEC with FEC additive to minimize coulombic losses from side-reactions

## Patch Sketch

```diff
--- capacity_fade_due_to_side-reactions_in_silicon_anodes_in_lithium-ion_batteries.before.py
+++ capacity_fade_due_to_side-reactions_in_silicon_anodes_in_lithium-ion_batteries.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Capacity fade in silicon anodes due to continuous electrolyte-reduction side-reactions on freshly-exposed electrode surfaces during cycling

+# Fix    : Use electrolyte formulation EC:DEC with FEC additive to minimize coulombic losses from side-reactions

+# Avoid  : Using EC:DEC without FEC additive leads to highest side-reaction losses

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Using EC:DEC without FEC additive leads to highest side-reaction losses

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Memory_Reasoning** → Reuse stable surface layers from prior cycles to limit fresh electrolyte exposure and capacity loss.
  - related fix: Reuse key-value (KV) caches from previous turns to avoid full recomputation, maintaining bounded context size and controlled inference cost.

