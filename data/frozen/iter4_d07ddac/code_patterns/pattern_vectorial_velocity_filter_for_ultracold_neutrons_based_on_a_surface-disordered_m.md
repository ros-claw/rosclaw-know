---
pattern_id: pattern_vectorial_velocity_filter_for_ultracold_neutrons_based_on_a_surface-disordered_m
schema_version: "2.0"
applicable_symptoms: [vectorial_velocity_filter_for_ultracold_neutrons_based_on_a_surface-disordered_m]
domain: World_Physics
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Ultracold neutron beams have high angular divergence, making it difficult to control velocity components for precision experiments.

**Domain**: `World_Physics`

## Symptom

Ultracold neutron beams have high angular divergence, making it difficult to control velocity components for precision experiments.

## Diagnosis

Use an absorbing-reflecting mirror system with surface disorder to exploit mixed phase space (regular skipping motion and random scattering) as a vectorial velocity filter, adjusting geometric parameters to control velocity component ranges.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use an absorbing-reflecting mirror system with surface disorder to exploit mixed phase space (regular skipping motion and random scattering) as a vectorial velocity filter, adjusting geometric parameters to control velocity component ranges.

## Code Target

_(no code target documented in source)_

## Fix

Use an absorbing-reflecting mirror system with surface disorder to exploit mixed phase space (regular skipping motion and random scattering) as a vectorial velocity filter, adjusting geometric parameters to control velocity component ranges.

## Patch Sketch

```diff
--- vectorial_velocity_filter_for_ultracold_neutrons_based_on_a_surface-disordered_m.before.py
+++ vectorial_velocity_filter_for_ultracold_neutrons_based_on_a_surface-disordered_m.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Ultracold neutron beams have high angular divergence, making it difficult to control velocity components for precision experiments.

+# Fix    : Use an absorbing-reflecting mirror system with surface disorder to exploit mixed phase space (regular skipping motion and random scattering) as a vectorial velocity filter, adjusting geometric parameters to control velocity component ranges.

+# Avoid  : Conventional neutron filters that rely solely on absorption or reflection without exploiting disorder-induced mixed phase space.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Conventional neutron filters that rely solely on absorption or reflection without exploiting disorder-induced mixed phase space.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Planning_Decision** → Closed-loop beam steering using real-time divergence feedback to iteratively correct velocity components.
  - related fix: Closed-loop reasoning: iteratively update belief state from real-time sensor feedback, evaluate actions, execute, observe, and update until task goal is achieved.

