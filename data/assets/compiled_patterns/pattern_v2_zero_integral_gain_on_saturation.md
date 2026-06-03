---
pattern_id: compiled_zero_integral_gain_on_saturation
schema_version: "2.0"
domain: Control_Locomotion
task_families: ['robotics_optimization']
embodiment_types: ['uav', 'manipulator', 'wheeled_robot', 'quadruped']
artifact_languages: ['python', 'cpp']
priority: 0
source_quality: A
source_ids:
  - experiment1__openevolve__claude-opus-4.6__Robotics_PIDTuning
  - experiment1__openevolve__deepseek-v3.2__Robotics_PIDTuning
  - experiment1__openevolve__gemini-3.1-pro-preview__Robotics_PIDTuning
  - experiment1__openevolve__glm-5__Robotics_PIDTuning
  - experiment1__openevolve__gpt-5.4__Robotics_PIDTuning
  - experiment1__openevolve__grok-4.20__Robotics_PIDTuning
  - experiment1__openevolve__seed-2.0-pro__Robotics_PIDTuning
  - experiment2__openevolve__claude-opus-4.6__Robotics_PIDTuning
  # …and 1 more (truncated)
evidence:
  n: 9
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Actuator saturates while integral term keeps accumulating; recovery after setpoint change is slow

## Symptom

Actuator saturates while integral term keeps accumulating; recovery after setpoint change is slow.

## Diagnosis

Likely cause(s):
- unconditional integration during saturation
- missing back-calculation term
- integral gain too high for current actuator range

When an actuator output is at saturation, allowing the integral term to keep accumulating produces windup.  Successful PID-tuning runs disable the integral channel entirely for axes where the actuator is regularly saturated.

## Preconditions

- Observable signal: actuator output clipped to limit for many control steps
- Observable signal: overshoot grows after error persists
- Observable signal: settling time blows up after setpoint reversal
- Symbols present in the editable artifact: `Ki_theta`, `Ki_x`, `Ki_z`

## Next Experiment

- Set the named parameter(s) to zero: set parameter to zero on Ki_theta; set parameter to zero on Ki_x; set parameter to zero on Ki_z.

## Code Target

Search the editable artifact for these identifiers and treat their definitions as the patch site: `Ki_theta`, `Ki_x`, `Ki_z`.

## Patch Sketch

```python
# Zero out the named parameter(s) in your candidate dict.
# Example shape (replace identifier with the one in your code):
best_gains["<param>"] = 0.0   # was nonzero in baseline
```

## Expected Verifier Signal

- feasibility stays valid, overshoot decreases, settling time after setpoint changes goes down.

## Anti-pattern

- do not simply increase Ki when output is already saturated
- do not remove the output limiter

## Contraindications

- do not raise Ki to compensate — the integrator will wind up again on the next saturation event
- do not simply increase Ki when output is already saturated
- do not remove the output limiter

## Cross-domain analogy

**Learning_Training**: same shape as `clip_grad_norm_` — an upper bound that only fires when the magnitude exceeds a known physical limit.  Both are 'stop the integration when downstream saturates'.
