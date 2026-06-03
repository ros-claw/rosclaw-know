# Sprint 13 — catalog expansion acceptance

- new FailureMode entries added to YAML: **4** (skipped 0 already present)
- new FailureMode nodes added to graph: **4**
- new FixPattern nodes added to graph: **7**
- FIXES edges added to graph: **7**

## Coverage: 8/8 event_types

| event_type | covered? | fix patterns |
|---|---|---|
| actuator_saturation | ✅ | compiled_controller_output_clamp, compiled_zero_integral_gain_on_saturation |
| collision | ✅ | compiled_collision_avoidance_replan |
| controller_error | ✅ | compiled_gradient_clip_norm, compiled_mpc_replan_on_state_error, compiled_zero_integral_gain_on_saturation |
| joint_limit_violation | ✅ | compiled_joint_limit_planner_clamp |
| safety_stop | ✅ | compiled_safety_stop_supervised_resume |
| sensor_outlier | ✅ | compiled_sensor_median_filter_guard |
| task_timeout | ✅ | compiled_add_time_budget, compiled_generic_time_budget, compiled_warm_start_from_prior_best |
| trajectory_deviation | ✅ | compiled_gradient_clip_norm, compiled_mpc_replan_on_state_error |

## How this happens

Sprint 10 made the `event_type → pattern_id` transfer table an auto-derived join over `FailureMode.id / normalized_symptom / name` ↔ `FixPattern.failure_ids`. Sprint 13 widens that join domain-side: with FixPatterns for all eight event_types' anchor failures, the table now covers the full canonical set.