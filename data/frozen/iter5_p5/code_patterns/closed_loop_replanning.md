---
pattern_id: closed_loop_replanning
safety_label: Oscillation_Divergence
applicable_symptoms: [closed_loop_replanning]
domain: Planning_Decision
source: curated
---

# Model-predictive control (MPC) and receding-horizon planning drift away from ground truth when the internal dynamics model mismatches real-world friction, terrain, or disturbance; the predicted state diverges from the measured state over the planning horizon, causing unbounded tracking error unless the plan is replanned in closed loop with online state feedback and shorter replan intervals

**Domain**: `Planning_Decision`
**Safety label**: `Oscillation_Divergence`

## Fix

Replace the open-loop planner with a Model-Predictive Control loop: re-solve a horizon-H optimization every dt using the latest measurement, execute only the first action, then re-solve. Keep dt ≤ system latency.

## Anti-pattern

Compensating for tracking error by adding feed-forward terms tuned offline — the offline tuning never anticipates the actual disturbance profile.

## Cross-domain analogies (curated)

- **Learning_Training** → Same closed-loop principle as supervised → RL fine-tuning: don't trust your offline model, re-measure under the deployment distribution.
  - related fix: Treat each MPC step as a one-step on-policy correction, the way RL fine-tuning corrects a supervised base.

## Patch

```diff
--- closed_loop_replanning.before.py+++ closed_loop_replanning.after.py@@ -1,3 +1,4 @@-plan = solve_once(initial_state)   # open loop
-for u in plan:
-    execute(u)
+while not done:
+    state = sense()                # close the loop
+    plan  = solve_horizon(state, H)
+    execute(plan[0])               # discard the rest, re-solve

```
