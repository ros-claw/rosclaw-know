---
pattern_id: terrain_aware_locomotion
safety_label: Tracking_Error
applicable_symptoms: [terrain_aware_locomotion]
domain: Control_Locomotion
source: curated
---

# Legged-robot trot gait diverges on uneven or slippery terrain — foot-slip events compound center-of-mass tracking error each cycle until the robot falls

**Domain**: `Control_Locomotion`
**Safety label**: `Tracking_Error`

## Fix

Pick one of three orthogonal mitigations matched to the failure mode: (a) FOOT-CONTACT-AWARE MPC — predict contact events 1-2 stance phases ahead and constrain swing-foot placement to minimize estimated slip; (b) DOMAIN-RANDOMIZED RL — re-train the policy with friction-coefficient and terrain-height perturbations so it doesn't overfit a clean ground model; (c) SLIP-DETECTION REFLEX — monitor IMU + leg-encoder residuals during each stance; on detected slip, preempt the planned swing foot trajectory and replan toward a stable footstep within the support polygon.

## Anti-pattern

Cranking up joint-PD gains to chase the tracking error — this amplifies slip-induced impulses and accelerates fall, because the underlying issue is contact uncertainty, not actuator compliance.

## Cross-domain analogies (curated)

- **Learning_Training** → Domain randomization in RL training is the same idea as model-mismatch robustification in classical MPC: don't overfit one operating model.
  - related fix: If the controller assumes friction=0.7 and the test surface is friction=0.3, augment training distributions accordingly.
- **Memory_Reasoning** → Slip detection via residual monitoring is analogous to anomaly detection in time-series data — both watch for deviation from expected dynamics.
  - related fix: Use the same threshold + windowing logic that you'd apply to a Kalman innovation sequence.

## Patch

```diff
--- terrain_aware_locomotion.before.py+++ terrain_aware_locomotion.after.py@@ -1,3 +1,6 @@ def step(obs):
-    foot_target = mpc_solve(obs.com_state)  # assumes flat ground
+    foot_target = mpc_solve(obs.com_state,
+                            contact_pred=predict_contacts(obs))
+    if detect_slip(obs.imu_residual, obs.encoder_residual):
+        foot_target = replan_for_stable_footstep(obs.support_polygon)
     return foot_target

```
