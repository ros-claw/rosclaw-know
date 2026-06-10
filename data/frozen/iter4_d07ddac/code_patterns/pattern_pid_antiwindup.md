---
pattern_id: pattern_pid_antiwindup
schema_version: "2.0"
applicable_symptoms: [pid_antiwindup]
domain: Control_Locomotion
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# When the commanded velocity exceeds the actuator capacity for more than a few sample periods, the integral term grows unboundedly, causing overshoot and torque oscillation after the setpoint is reached.

**Domain**: `Control_Locomotion`

## Symptom

When the commanded velocity exceeds the actuator capacity for more than a few sample periods, the integral term grows unboundedly, causing overshoot and torque oscillation after the setpoint is reached.

## Diagnosis

Output-saturated integral clamp: detect when the controller output is at the saturation limit and stop integrating while saturated, using back-calculation to bleed the integrator toward a value consistent with the saturated output.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Output-saturated integral clamp: detect when the controller output is at the saturation limit and stop integrating while saturated, using back-calculation to bleed the integrator toward a value consistent with the saturated output.

## Code Target

_(no code target documented in source)_

## Fix

Output-saturated integral clamp: detect when the controller output is at the saturation limit and stop integrating while saturated, using back-calculation to bleed the integrator toward a value consistent with the saturated output.

## Patch Sketch

```diff
--- pid_antiwindup.before.py
+++ pid_antiwindup.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: When the commanded velocity exceeds the actuator capacity for more than a few sample periods, the integral term grows unboundedly, causing overshoot and torque oscillation after the setpoint is reached.

+# Fix    : Output-saturated integral clamp: detect when the controller output is at the saturation limit and stop integrating while saturated, using back-calculation to bleed the integrator toward a value consistent with the saturated output.

+# Avoid  : Disabling the integral term entirely during saturation, which causes steady-state error that the proportional term alone cannot eliminate.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Disabling the integral term entirely during saturation, which causes steady-state error that the proportional term alone cannot eliminate.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Systems_Compute** → Use event-driven anti-windup clamping triggered by actuator saturation to reset the integral term.
  - related fix: Deploy event-driven bots that react to tool-triggered events and user messages to automate tasks such as CI/CD, code review, and issue triage.
- **Memory_Reasoning** → Use a recency-weighted memory of past saturation events to clamp integral accumulation.
  - related fix: Maintain a structured memory of historical visual observations weighted by temporal recency and spatial novelty, so that past frames influence current reasoning and planning.
- **Memory_Reasoning** → Use a bounded memory of recent errors to clamp the integral term, preventing unbounded growth.
  - related fix: Maintain a bounded internal state (e.g., dynamic bounded memory queue) that stores recent observations and actions, and fuse it with current observation via attention or recurrent layer before action selection.

