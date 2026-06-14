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

Use a latency-aware saturated PID with three named mechanisms: (1) ANTI-WINDUP CLAMP — clamp the actuator command and apply back-calculation so the integrator stops accumulating while the actuator is saturated; (2) DERIVATIVE-ON-MEASUREMENT — compute the derivative from the filtered measured state, not from setpoint error, to avoid derivative kick under 30 ms loop delay; (3) GAIN SCHEDULING — reduce Kp/Ki/Kd or switch to a conservative gain set whenever the sensor-to-actuator delay dominates the loop bandwidth (rule of thumb: keep crossover below 1/(8*tau_d)).

## Anti-pattern

Increasing Kp or Kd alone to chase the oscillation — under 30 ms loop latency the additional gain crosses 0 dB at a higher frequency, moving the resonance INTO the closed-loop bandwidth and amplifying the oscillation instead of damping it.

## Cross-domain analogies (curated)

- **Memory_Reasoning** → Loop latency in physical control is the same problem as KV-cache staleness in long-context attention: by the time you act on old state, the world has moved on.
  - related fix: Always model the action delay explicitly when designing a feedback loop — Smith predictors are the controller-side analogue of speculative decoding's draft model.
- **Learning_Training** → Gradient clipping under unbounded loss is the SGD analogue of clamping a PID output: both bound the per-step delta to prevent unbounded integration.
  - related fix: If your PID needs anti-windup, your training loop probably needs gradient clipping for the same structural reason.

## Patch

```diff
--- pid_joint_latency_oscillation.before.py+++ pid_joint_latency_oscillation.after.py@@ -1,5 +1,26 @@-def pid_step(setpoint, measurement, dt, integ):
-    err = setpoint - measurement       # measurement is 30ms stale
-    integ += err * dt                  # keeps accumulating during deadtime
-    tau = Kp*err + Ki*integ + Kd*derr
-    return tau
+def pid_step(setpoint, measurement, velocity, dt, state, cfg):
+    # Fix : latency-aware PID = anti-windup clamp + 
+    #       derivative-on-measurement + gain scheduling
+    q_fb = measurement + velocity * cfg.deadtime_s  # latency-aware feedback
+    err = setpoint - q_fb
+
+    # Derivative-on-measurement (filtered), not on setpoint error
+    dmeas = (q_fb - state.q_fb_prev) / dt
+    alpha = dt / (dt + cfg.d_filter_s)
+    dmeas_filt = alpha * dmeas + (1 - alpha) * state.dmeas_filt
+
+    tau_uncl = cfg.Kp*err + cfg.Ki*state.integ - cfg.Kd*dmeas_filt
+    tau = clamp(tau_uncl, -cfg.tau_max, cfg.tau_max)  # anti-windup clamp
+
+    # Back-calculation anti-windup / conditional integration
+    sat_err = tau - tau_uncl
+    if not (tau != tau_uncl and same_sign(err, tau_uncl)):
+        state.integ += cfg.Ki * err * dt
+    state.integ += cfg.kaw * sat_err * dt
+    state.integ = clamp(state.integ, -cfg.integ_limit, cfg.integ_limit)
+
+    # Gain scheduling: with 30 ms delay, reduce Kp/Kd so crossover
+    # stays below ~1/(8*tau_d) and phase margin stays healthy
+    state.q_fb_prev = q_fb
+    state.dmeas_filt = dmeas_filt
+    return tau, state

```
