---
pattern_id: pattern_spot-on_a_checkpointing_framework_for_fault-tolerant_long-running_workloads_on_c
schema_version: "2.0"
applicable_symptoms: [spot-on_a_checkpointing_framework_for_fault-tolerant_long-running_workloads_on_c]
domain: Systems_Compute
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Long-running jobs on spot instances fail unpredictably due to evictions, causing wasted computation and increased costs.

**Domain**: `Systems_Compute`

## Symptom

Long-running jobs on spot instances fail unpredictably due to evictions, causing wasted computation and increased costs.

## Diagnosis

Use a generic checkpoint/restart framework (Spot-on) that supports both application-specific and transparent checkpointing, compatible with major cloud vendors.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Use a generic checkpoint/restart framework (Spot-on) that supports both application-specific and transparent checkpointing, compatible with major cloud vendors.

## Code Target

_(no code target documented in source)_

## Fix

Use a generic checkpoint/restart framework (Spot-on) that supports both application-specific and transparent checkpointing, compatible with major cloud vendors.

## Patch Sketch

```diff
--- spot-on_a_checkpointing_framework_for_fault-tolerant_long-running_workloads_on_c.before.py
+++ spot-on_a_checkpointing_framework_for_fault-tolerant_long-running_workloads_on_c.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Long-running jobs on spot instances fail unpredictably due to evictions, causing wasted computation and increased costs.

+# Fix    : Use a generic checkpoint/restart framework (Spot-on) that supports both application-specific and transparent checkpointing, compatible with major cloud vendors.

+# Avoid  : Running on on-demand instances (higher cost) or using only application-specific checkpointing (higher runtime overhead).

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Running on on-demand instances (higher cost) or using only application-specific checkpointing (higher runtime overhead).

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Planning_Decision** → Use an LLM-based advisor to monitor job progress and spot instance risk, triggering checkpointing only when eviction is imminent.
  - related fix: Use an LLM-based Advisor module that continuously monitors system state and task progress, evaluating contextual cues (e.g., unexpected sensor readings, partial failures) to issue a replanning request only when necessary.
- **Control_Locomotion** → Use lightweight checkpointing at high frequency to enable quick job resumption after eviction.
  - related fix: Use a lightweight MLP or RNN policy trained via RL in simulation, executed at 50-100 Hz for direct joint-level torque/position commands.
- **Memory_Reasoning** → Maintain a bounded job state checkpoint queue to resume from recent evictions.
  - related fix: Maintain a bounded internal state (e.g., dynamic bounded memory queue) that stores recent observations and actions, and fuse it with current observation via attention or recurrent layer before action selection.

