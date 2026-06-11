---
pattern_id: pattern_an_algorithmic_safety_vest_for_li-ion_batteries_during_fast_charging
schema_version: "2.0"
applicable_symptoms: [an_algorithmic_safety_vest_for_li-ion_batteries_during_fast_charging]
domain: Control_Locomotion
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Fast charging of Li-ion batteries causes accelerated aging due to lithium plating, SEI growth, and particle cracking, which depend on unmeasurable variables like overpotential and concentration gradient.

**Domain**: `Control_Locomotion`

## Symptom

Fast charging of Li-ion batteries causes accelerated aging due to lithium plating, SEI growth, and particle cracking, which depend on unmeasurable variables like overpotential and concentration gradient.

## Diagnosis

CC-CVησT (VEST) charging: a constant current constant voltage scheme that imposes constraints on plating potential (η) and mechanical stress (σ) using any battery model, with safety margins for uncertainties.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

CC-CVησT (VEST) charging: a constant current constant voltage scheme that imposes constraints on plating potential (η) and mechanical stress (σ) using any battery model, with safety margins for uncertainties.

## Code Target

_(no code target documented in source)_

## Fix

CC-CVησT (VEST) charging: a constant current constant voltage scheme that imposes constraints on plating potential (η) and mechanical stress (σ) using any battery model, with safety margins for uncertainties.

## Patch Sketch

```diff
--- an_algorithmic_safety_vest_for_li-ion_batteries_during_fast_charging.before.py
+++ an_algorithmic_safety_vest_for_li-ion_batteries_during_fast_charging.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Fast charging of Li-ion batteries causes accelerated aging due to lithium plating, SEI growth, and particle cracking, which depend on unmeasurable variables like overpotential and concentration gradient.

+# Fix    : CC-CVησT (VEST) charging: a constant current constant voltage scheme that imposes constraints on plating potential (η) and mechanical stress (σ) using any battery model, with safety margins for uncertainties.

+# Avoid  : Physics-based models with optimal control algorithms are complex and limit implementation.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Physics-based models with optimal control algorithms are complex and limit implementation.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Systems_Compute** → Constrain charging to a small set of canonical current profiles to avoid unmeasurable overpotential spikes.
  - related fix: Pad batches to a small set of canonical shapes and force a single trace shape via jit with pre-allocated buffers and XLA flags, optionally tuning XLA_TPU_BUFFER_PADDING_RATIO.
- **Planning_Decision** → Build an explicit differentiable model of internal battery states to guide charging policy.
  - related fix: Build an explicit semantic map in the world reference frame using differentiable pinhole camera projection, then feed it into a control policy to generate continuous velocity commands.

