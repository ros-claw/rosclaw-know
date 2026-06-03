---
pattern_id: compiled_controller_output_clamp
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
  - experiment1__openevolve__qwen3-coder-next__Robotics_PIDTuning
  - experiment1__openevolve__seed-2.0-pro__Robotics_PIDTuning
  # …and 6 more (truncated)
evidence:
  n: 14
  avg_uplift: 0.0000
  win_rate: 0.0000
  hint_use_rate: 0.0000
---

# Final controller command is not bounded before it reaches the actuator interface

## Symptom

Final controller command is not bounded before it reaches the actuator interface.

## Diagnosis

Likely cause(s):
- clamp removed during debugging
- feed-forward path bypasses clamp

Successful runs explicitly clamp the controller command to the actuator's physical range before it leaves the control function.  Relying on downstream code to clip is a frequent source of saturation-induced instability.

## Preconditions

- Observable signal: command magnitude tracks raw PID output
- Observable signal: actuator saturation flag never set by driver
- Symbols present in the editable artifact: `T_cmd`, `desired_pitch`

## Next Experiment

- Clamp the controller output before it leaves the function: added output clamp on desired_pitch; added output clamp on T_cmd.

## Code Target

Search the editable artifact for these identifiers and treat their definitions as the patch site: `T_cmd`, `desired_pitch`.

## Patch Sketch

```python
# Clamp the controller output BEFORE the actuator-lag filter.
out = max(min_value, min(max_value, out))
# or numpy:
out = np.clip(out, -bound, bound)
```

## Expected Verifier Signal

- feasibility stays valid even on aggressive maneuvers; thrust / torque never exceeds its bound.

## Anti-pattern

- do not implement clamp inside the integrator step — keep it on the output

## Contraindications

- clamps must be applied *before* the actuator-lag filter, not after, or the command will spike at the next sample
- do not implement clamp inside the integrator step — keep it on the output
