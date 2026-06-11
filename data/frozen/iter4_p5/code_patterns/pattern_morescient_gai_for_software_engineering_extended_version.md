---
pattern_id: pattern_morescient_gai_for_software_engineering_extended_version
schema_version: "2.0"
applicable_symptoms: [morescient_gai_for_software_engineering_extended_version]
domain: Learning_Training
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Existing LLM-based code models are trained exclusively on syntactic facets of software, leading to low trustworthiness in tasks dependent on software semantics.

**Domain**: `Learning_Training`

## Symptom

Existing LLM-based code models are trained exclusively on syntactic facets of software, leading to low trustworthiness in tasks dependent on software semantics.

## Diagnosis

Train GAI models on both static code and dynamic execution observations (semantic facets) using a structured observation platform.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Train GAI models on both static code and dynamic execution observations (semantic facets) using a structured observation platform.

## Code Target

_(no code target documented in source)_

## Fix

Train GAI models on both static code and dynamic execution observations (semantic facets) using a structured observation platform.

## Patch Sketch

```diff
--- morescient_gai_for_software_engineering_extended_version.before.py
+++ morescient_gai_for_software_engineering_extended_version.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Existing LLM-based code models are trained exclusively on syntactic facets of software, leading to low trustworthiness in tasks dependent on software semantics.

+# Fix    : Train GAI models on both static code and dynamic execution observations (semantic facets) using a structured observation platform.

+# Avoid  : Training only on static code syntax without execution semantics.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Training only on static code syntax without execution semantics.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Control_Locomotion** → Train a code model on both syntax and execution traces for semantic grounding.
  - related fix: Train a visual locomotion policy that maps camera images and proprioception to joint actions, enabling real-time foot placement adaptation to obstacles.
- **Memory_Reasoning** → Build hierarchical semantic code representations from dynamic program analysis to enable multi-level reasoning.
  - related fix: Build hierarchical scene graph incrementally from semantic object map, with layers for objects, regions, rooms, and functional zones, updated online as new observations arrive.
- **Memory_Reasoning** → Convert human demonstrations into semantic QA pairs to fine-tune LLMs for reasoning over software semantics.
  - related fix: Convert human demonstration trajectories into multi-turn QA pairs to fine-tune VLM for chain-of-thought reasoning.

