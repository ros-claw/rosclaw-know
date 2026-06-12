---
pattern_id: motion_blur_imu_aided_deblur
safety_label: Image_Motion_Blur
applicable_symptoms: [motion_blur_imu_aided_deblur]
domain: Perception_Vision
source: curated
---

# Onboard RGB or monocular camera mounted on a moving aerial / mobile platform (UAV, quadrotor, drone, inspection robot) produces motion-blurred frames during fast relative velocity to the inspection target; downstream defect detection or object classification recall drops 40-60 % versus the stationary or hover baseline, because the blur extent within the exposure window exceeds the detector's invariance to blur, even when focal length and ISO are held fixed across the flyby phase

**Domain**: `Perception_Vision`
**Safety label**: `Image_Motion_Blur`

## Fix

Three layered fixes, applied in order of cost: (1) shorten shutter (e.g. 1/500 s) and bump ISO + denoise — eliminates blur at the source for fast platforms with adequate light. (2) Replace continuous flybys with hover-and-stare waypoints near the inspection target — at v≈0 the blur extent is zero. (3) When neither is feasible (low light, fixed mission profile), estimate a per-frame blur kernel from the IMU-predicted camera motion during the exposure window and Wiener-deconvolve or feed the predicted kernel to a learned deblur network (e.g. DeblurGAN, NAFNet).

## Anti-pattern

Naively bumping ISO without shortening exposure — blur stays the same but noise grows, and the detection network is now robust to neither. Or training only on stationary images and hoping the network generalizes to blur.

## Cross-domain analogies (curated)

- **Systems_Compute** → Same shape as adaptive batch sizing under variable load: set the exposure (batch) based on the live state of the system (platform velocity), not a fixed default.
  - related fix: Bind capture parameters to the IMU-estimated motion rather than mission-time constants — the right exposure for the hover phase is wrong for the flyby phase.

## Patch

```diff
--- motion_blur_imu_aided_deblur.before.py+++ motion_blur_imu_aided_deblur.after.py@@ -1,3 +1,8 @@-# fixed exposure regardless of platform velocity → blur scales with v
-frame = camera.capture(exposure_s=1.0/120, iso=400)
+# adapt exposure to platform velocity AND deconvolve IMU-predicted blur
+v_rel = imu.estimate_rel_velocity(target_pose)
+expo_s = min(0.5 / (v_rel + 1e-3), 1.0/500)   # cap blur extent
+frame = camera.capture(exposure_s=expo_s, iso=auto)
+if v_rel > BLUR_THRESHOLD:
+    kernel = imu.predict_blur_kernel(camera, expo_s)
+    frame = wiener_deconvolve(frame, kernel)
 boxes = detector.predict(frame)

```
