---
pattern_id: pattern_gaussian_splatting_on_the_move_blur_and_rolling_shutter_compensation_for_natural
schema_version: "2.0"
applicable_symptoms: [gaussian_splatting_on_the_move_blur_and_rolling_shutter_compensation_for_natural]
domain: Perception_Vision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# 3D Gaussian Splatting scene reconstruction fails with handheld video due to motion blur and rolling shutter distortion from natural camera motion.

**Domain**: `Perception_Vision`

## Symptom

3D Gaussian Splatting scene reconstruction fails with handheld video due to motion blur and rolling shutter distortion from natural camera motion.

## Diagnosis

Model physical image formation with non-static camera poses during exposure, using VIO velocity estimates and a differentiable rendering pipeline with screen-space approximation for rolling shutter and motion blur.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Model physical image formation with non-static camera poses during exposure, using VIO velocity estimates and a differentiable rendering pipeline with screen-space approximation for rolling shutter and motion blur.

## Code Target

_(no code target documented in source)_

## Fix

Model physical image formation with non-static camera poses during exposure, using VIO velocity estimates and a differentiable rendering pipeline with screen-space approximation for rolling shutter and motion blur.

## Patch Sketch

```diff
--- gaussian_splatting_on_the_move_blur_and_rolling_shutter_compensation_for_natural.before.py
+++ gaussian_splatting_on_the_move_blur_and_rolling_shutter_compensation_for_natural.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: 3D Gaussian Splatting scene reconstruction fails with handheld video due to motion blur and rolling shutter distortion from natural camera motion.

+# Fix    : Model physical image formation with non-static camera poses during exposure, using VIO velocity estimates and a differentiable rendering pipeline with screen-space approximation for rolling shutter and motion blur.

+# Avoid  : Standard 3DGS assumes static camera per frame, ignoring motion blur and rolling shutter.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Standard 3DGS assumes static camera per frame, ignoring motion blur and rolling shutter.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **World_Physics** → Simulate natural camera motion in training with configurable rolling shutter and blur parameters.
  - related fix: Use photorealistic 3D environment simulation with configurable sensors and physics integration from Habitat Simulator
- **Planning_Decision** → Hierarchical decomposition: high-level selects stable keyframes, low-level corrects blur and rolling shutter per segment.
  - related fix: Hierarchical high- and low-level policies: high-level selects subgoals from visual and linguistic inputs, low-level executes continuous motor commands to reach subgoals; modularized training decouples reasoning and imitation.

