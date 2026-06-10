---
pattern_id: pattern_from_two_rolling_shutters_to_one_global_shutter
schema_version: "2.0"
applicable_symptoms: [from_two_rolling_shutters_to_one_global_shutter]
domain: Perception_Vision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Rolling shutter distortion occurs when camera moves during image capture, causing image warping that standard single-camera methods cannot correct without scene structure assumptions.

**Domain**: `Perception_Vision`

## Symptom

Rolling shutter distortion occurs when camera moves during image capture, causing image warping that standard single-camera methods cannot correct without scene structure assumptions.

## Diagnosis

Use two cameras with opposite rolling shutter directions and solve geometric constraints from sparse point correspondences to undistort images.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use two cameras with opposite rolling shutter directions and solve geometric constraints from sparse point correspondences to undistort images.

## Code Target

_(no code target documented in source)_

## Fix

Use two cameras with opposite rolling shutter directions and solve geometric constraints from sparse point correspondences to undistort images.

## Patch Sketch

```diff
--- from_two_rolling_shutters_to_one_global_shutter.before.py
+++ from_two_rolling_shutters_to_one_global_shutter.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Rolling shutter distortion occurs when camera moves during image capture, causing image warping that standard single-camera methods cannot correct without scene structure assumptions.

+# Fix    : Use two cameras with opposite rolling shutter directions and solve geometric constraints from sparse point correspondences to undistort images.

+# Avoid  : Single-camera rolling shutter correction methods that rely on specific scene structure or require dense correspondences.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Single-camera rolling shutter correction methods that rely on specific scene structure or require dense correspondences.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Planning_Decision** → Condition the camera model on per-row motion constraints to correct rolling shutter warp.
  - related fix: Capability-conditioned navigation (CapNav): integrate agent-specific physical constraints (e.g., dimensions, turning radius) into spatial reasoning and path planning.

