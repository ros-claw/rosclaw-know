---
pattern_id: pid_joint_latency_oscillation
safety_label: PID_Joint_Latency_Oscillation
applicable_symptoms: [pid_joint_latency_oscillation]
domain: Control_Locomotion
source: curated
---

# Robotic-arm joint feedback control diverges into sustained oscillation when sensor-to-actuator loop deadtime around 30 ms exceeds the inverse of the joint's mechanical bandwidth, with stale measurements compounding the tracking-error response each control cycle

**Domain**: `Control_Locomotion`
**Safety label**: `PID_Joint_Latency_Oscillation`

## Fix

Combine three remedies for latency-induced PID oscillation: (a) SMITH PREDICTOR — explicitly compensate the deadtime by feeding the controller a predicted-future plant output instead of the raw measurement; (b) ANTI-WINDUP — conditional integration that stops accumulating when the actuator is saturated AND the error direction would push further into saturation; (c) GAIN MARGIN BUDGET — reduce Kp until the loop's gain at the deadtime frequency 1/(2*tau_d) is below 0 dB, sacrificing bandwidth for stability.

## Anti-pattern

Increasing Kp alone to chase the oscillation — under 30 ms loop latency the additional gain crosses 0 dB at a higher frequency, moving the resonance INTO the closed-loop bandwidth and amplifying the oscillation instead of damping it.

## Cross-domain analogies (curated)

- **Memory_Reasoning** → Loop latency in physical control is the same problem as KV-cache staleness in long-context attention: by the time you act on old state, the world has moved on.
  - related fix: Always model the action delay explicitly when designing a feedback loop — Smith predictors are the controller-side analogue of speculative decoding's draft model.
- **Learning_Training** → Gradient clipping under unbounded loss is the SGD analogue of clamping a PID output: both bound the per-step delta to prevent unbounded integration.
  - related fix: If your PID needs anti-windup, your training loop probably needs gradient clipping for the same structural reason.

## Patch

```diff
--- pid_joint_latency_oscillation.before.py+++ pid_joint_latency_oscillation.after.py@@ -1,5 +1,9 @@-def pid_step(setpoint, measurement, dt, integ):
-    err = setpoint - measurement       # measurement is 30ms stale
-    integ += err * dt                  # keeps accumulating during deadtime
-    tau = Kp*err + Ki*integ + Kd*derr
+def pid_step(setpoint, measurement, dt, integ, deadtime_s):
+    predicted = smith_predict(measurement, deadtime_s)  # advance past loop delay
+    err = setpoint - predicted
+    tau_uncl = Kp*err + Ki*integ + Kd*derr
+    tau = torch.clamp(tau_uncl, -tau_max, tau_max)
+    saturated = tau != tau_uncl
+    if not (saturated and same_sign(err, tau_uncl)):
+        integ += err * dt              # conditional integration
     return tau

```
