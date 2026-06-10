---
pattern_id: pattern_developers_perception_of_peer_code_review_in_research_software_development
schema_version: "2.0"
applicable_symptoms: [developers_perception_of_peer_code_review_in_research_software_development]
domain: Systems_Compute
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
  hint_use_rate: 0.0
---

# Research software developers lack formal process, proper organization, and adequate people to perform peer code reviews, leading to lower confidence in software correctness.

**Domain**: `Systems_Compute`

## Symptom

Research software developers lack formal process, proper organization, and adequate people to perform peer code reviews, leading to lower confidence in software correctness.

## Diagnosis

Adopt lightweight, asynchronous code review tools and establish minimal review checklists tailored to research software.

## Preconditions

_(no preconditions documented in source)_

## Next Experiment

Adopt lightweight, asynchronous code review tools and establish minimal review checklists tailored to research software.

## Code Target

_(no code target documented in source)_

## Fix

Adopt lightweight, asynchronous code review tools and establish minimal review checklists tailored to research software.

## Patch Sketch

```diff
--- developers_perception_of_peer_code_review_in_research_software_development.before.py
+++ developers_perception_of_peer_code_review_in_research_software_development.after.py
@@ -1,2 +1,4 @@
-# --- BEFORE (vulnerable to the symptom below) ---

+# --- AFTER (ROSCLAW heuristic graft) ---

 # Symptom: Research software developers lack formal process, proper organization, and adequate people to perform peer code reviews, leading to lower confidence in software correctness.

+# Fix    : Adopt lightweight, asynchronous code review tools and establish minimal review checklists tailored to research software.

+# Avoid  : Relying on informal, ad-hoc review practices without structured process or dedicated reviewer allocation.

```

## Expected Verifier Signal

_(no expected verifier signal documented in source)_

## Anti-pattern

Relying on informal, ad-hoc review practices without structured process or dedicated reviewer allocation.

## Contraindications

_(no contraindications documented in source)_

## Cross-domain analogies

- **Control_Locomotion** → Implement a separate automated review policy with a static analysis critic that overrides manual review when risk exceeds a threshold.
  - related fix: Train a separate RL safety-shielding policy with a safety critic and intervention logic that overrides the nominal controller when risk exceeds a threshold.

