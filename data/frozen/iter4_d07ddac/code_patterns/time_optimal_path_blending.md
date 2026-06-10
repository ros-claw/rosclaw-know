---
pattern_id: time_optimal_path_blending
safety_label: Robot_Cycle_Time_Inflation
applicable_symptoms: [time_optimal_path_blending]
domain: Control_Locomotion
source: curated
---

# Joint-space trajectories that decelerate to zero at every via-point inflate cycle time by 40-60 % even when via-points are colinear

**Domain**: `Control_Locomotion`
**Safety label**: `Robot_Cycle_Time_Inflation`

## Fix

Use Time-Optimal Path Parameterization (TOPP-RA or equivalent) over the full multi-via-point path so the velocity profile is computed against joint torque/velocity/acceleration limits globally, not per-segment. For colinear via-points let the blender preserve a non-zero pass velocity (blend_radius > 0). Where TOPP is unavailable, fall back to jerk-limited S-curve profiles per segment with explicit blend-velocity continuity at via-points — the arm never stops at intermediate poses.

## Anti-pattern

Calling MoveIt's joint_trajectory_controller with each via-point as a separate goal — the controller decelerates to zero at every intermediate pose because each goal is a stop-condition, even though the geometric path could be traversed at constant speed.

## Cross-domain analogies (curated)

- **Planning_Decision** → Solving each via-point in isolation is the same anti-pattern as greedy local search on a global schedule — the local optimum (full deceleration at each goal) is far from the global one (constant speed through colinear segments).
  - related fix: Plan the velocity profile over the FULL multi-via-point path, not segment-by-segment. Hand the planner the joint-level kinematic limits, not just the geometric waypoints.

## Patch

```diff
--- time_optimal_path_blending.before.py+++ time_optimal_path_blending.after.py@@ -1,3 +1,10 @@-for via in via_points:
-    # each call decelerates to zero at `via` — wastes 60% of cycle
-    arm.move_to(via, velocity_scale=1.0)
+from toppra import TOPPRA
+# Compute a single time-optimal velocity profile across ALL via-points,
+# preserving non-zero pass velocity at colinear segments.
+profile = TOPPRA(
+    path=via_points,
+    vlim=arm.joint_velocity_limits,
+    alim=arm.joint_acceleration_limits,
+    blend_radius=0.05,                  # meters of blend overlap
+).compute_trajectory()
+arm.follow_trajectory(profile)         # never stops mid-path

```
