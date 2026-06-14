---
pattern_id: pattern_motion_blur_decomposition_with_cross-shutter_guidance
schema_version: "2.0"
applicable_symptoms: [motion_blur_decomposition_with_cross-shutter_guidance]
domain: Perception_Vision
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Motion blur decomposition from a single blurry image is highly ambiguous due to lack of temporal cues.

**Domain**: `Perception_Vision`

## Symptom

Motion blur decomposition from a single blurry image is highly ambiguous due to lack of temporal cues.

## Diagnosis

Use a rolling shutter image with ordered scanline-wise delay as cross-shutter guidance to disambiguate motion, via a deep network with reciprocal branches for temporal and contextual information.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use a rolling shutter image with ordered scanline-wise delay as cross-shutter guidance to disambiguate motion, via a deep network with reciprocal branches for temporal and contextual information.

## Code Target

_(no code target documented in source)_

## Fix

Use a rolling shutter image with ordered scanline-wise delay as cross-shutter guidance to disambiguate motion, via a deep network with reciprocal branches for temporal and contextual information.

## Patch Sketch

```diff
--- motion_blur_decomposition_with_cross-shutter_guidance.before.py
+++ motion_blur_decomposition_with_cross-shutter_guidance.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Motion blur decomposition from a single blurry image is highly ambiguous due to lack of temporal cues.

+# Fix    : Use a rolling shutter image with ordered scanline-wise delay as cross-shutter guidance to disambiguate motion, via a deep network with reciprocal branches for temporal and contextual information.

+# Avoid  : Using only a single blurry image without additional priors or neighboring frames.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Using only a single blurry image without additional priors or neighboring frames.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Learning_Training** → Use a discriminator to distinguish real sharp images from deblurred ones, training the deblurring network to fool it.
  - related fix: Adversarial Variational Autoencoder (aVAE) combining VAE with adversarial training: use a discriminator to distinguish real data from VAE-generated samples, and train the VAE encoder/decoder to fool the discriminator.
- **Learning_Training** → Use iterative blur synthesis from sharp images to train a deblurring network on its own output distribution.
  - related fix: Iteratively collect new data under the current policy's distribution, query the expert for corrective actions, and aggregate this data into the training set (DAgger).
- **Control_Locomotion** → Pre-train a library of motion priors via self-supervised learning to disambiguate blur decomposition.
  - related fix: Pre-train a library of versatile locomotion and interaction behaviors via reinforcement learning, decoupling skill acquisition from task planning.

