---
pattern_id: pattern_minimal_solvers_for_monocular_rolling_shutter_compensation_under_ackermann_motio
schema_version: "2.0"
applicable_symptoms: [minimal_solvers_for_monocular_rolling_shutter_compensation_under_ackermann_motio]
domain: Perception_Vision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Rolling shutter distortion in monocular automotive cameras causes image misalignment, and existing methods are too slow for real-time operation and limited to rotational motion.

**Domain**: `Perception_Vision`

## Symptom

Rolling shutter distortion in monocular automotive cameras causes image misalignment, and existing methods are too slow for real-time operation and limited to rotational motion.

## Diagnosis

Use a minimal solver with Ackermann motion model (2 motion parameters) and simplified depth assumption (2 parameters) to estimate rolling shutter camera motion from 4 line correspondences.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use a minimal solver with Ackermann motion model (2 motion parameters) and simplified depth assumption (2 parameters) to estimate rolling shutter camera motion from 4 line correspondences.

## Code Target

_(no code target documented in source)_

## Fix

Use a minimal solver with Ackermann motion model (2 motion parameters) and simplified depth assumption (2 parameters) to estimate rolling shutter camera motion from 4 line correspondences.

## Patch Sketch

```diff
--- minimal_solvers_for_monocular_rolling_shutter_compensation_under_ackermann_motio.before.py
+++ minimal_solvers_for_monocular_rolling_shutter_compensation_under_ackermann_motio.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Rolling shutter distortion in monocular automotive cameras causes image misalignment, and existing methods are too slow for real-time operation and limited to rotational motion.

+# Fix    : Use a minimal solver with Ackermann motion model (2 motion parameters) and simplified depth assumption (2 parameters) to estimate rolling shutter camera motion from 4 line correspondences.

+# Avoid  : Methods using blur kernel and line straightness that handle only rotational motion and are not real-time.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Methods using blur kernel and line straightness that handle only rotational motion and are not real-time.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Planning_Decision** → Two-phase framework: rapid global shutter correction builds a distortion map, then real-time inference reuses cached pixel offsets.
  - related fix: Two-phase framework: rapid exploration builds symbolic scene graphs, then neurosymbolic planner reuses cached task-location trajectories for efficient deployment.

