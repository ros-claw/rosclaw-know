# Sprint 9 ingest summary

- adapters fired: rosbag=1, isaac=1, mujoco=1, foxglove=1
- urdf files parsed: 1
- total events: 26
- distinct FailureMode: 21
- distinct embodiments observed: 2
- ConstraintPattern emitted: 18

## FailureMode (id · domain · severity · #occurrences · embodiments)

| id | domain | severity | n | embodiments |
|---|---|---|---:|---|
| failure_actuator_saturation_saturation_actuator_2 | Control_Locomotion | warning | 1 | quadrotor |
| failure_actuator_saturation_saturation_torque_rotor_a | Control_Locomotion | warning | 1 | quadrotor |
| failure_collision_with_environment_collision_quadrotor_rotor_a_obstacle | Control_Locomotion | warning | 1 | quadrotor |
| failure_collision_with_environment_collision_ur5_wrist_3_link_table | Control_Locomotion | safety_critical | 3 | ur5 |
| failure_controller_divergence_controller_attitude_pid | Control_Locomotion | warning | 1 | quadrotor |
| failure_controller_divergence_controller_attitude_pid_diverged | Control_Locomotion | warning | 1 | quadrotor |
| failure_controller_divergence_controller_attitude_pid_windup | Control_Locomotion | warning | 1 | quadrotor |
| failure_controller_divergence_controller_joint_trajectory_controller_windup | Control_Locomotion | warning | 1 | ur5 |
| failure_controller_divergence_controller_lstm_pid_diverged | Control_Locomotion | warning | 1 | ur5 |
| failure_controller_divergence_controller_nan_nan_in_ctrl | Control_Locomotion | safety_critical | 1 | quadrotor |
| failure_joint_limit_violation_joint_shoulder_pan | Control_Locomotion | safety_critical | 1 | ur5 |
| failure_joint_limit_violation_joint_shoulder_pan_joint | Control_Locomotion | safety_critical | 2 | ur5 |
| failure_safety_stop_triggered_emergency_stop | Control_Locomotion | safety_critical | 2 | ur5 |
| failure_sensor_outlier_sensor_front_realsense_dropout | Control_Locomotion | warning | 1 | ur5 |
| failure_sensor_outlier_sensor_wrist_ft_sensor_sensor_dropout | Control_Locomotion | warning | 1 | ur5 |
| failure_sensor_outlier_sensor_wrist_ft_sensor_sensor_outlier | Control_Locomotion | warning | 1 | ur5 |
| failure_sensor_outlier_sensor_wrist_imu_imu_spike | Control_Locomotion | warning | 1 | ur5 |
| failure_task_timeout_task_pick_apple_off_table | Control_Locomotion | warning | 2 | ur5 |
| failure_task_timeout_task_rollout_timeout | Control_Locomotion | warning | 1 | ur5 |
| failure_trajectory_follow_error_trajectory_g_42 | Control_Locomotion | warning | 1 | ur5 |
| failure_trajectory_follow_error_trajectory_g_99 | Control_Locomotion | warning | 1 | ur5 |

# Sprint 9 — cross-embodiment reuse report

- distinct embodiments observed: 2
- failure modes on ≥2 embodiments: 0
- patterns on ≥2 embodiments: 3

## Pattern reuse

| pattern_id | event_types | embodiments | cross-embodiment? |
|---|---|---|---|
| add_boundary_validation | collision, controller_error, joint_limit_violation, safety_stop, sensor_outlier | quadrotor, ur5 | ✅ |
| add_time_budget | task_timeout | ur5 |   |
| anti_windup | actuator_saturation, controller_error | quadrotor, ur5 | ✅ |
| controller_output_clamp | actuator_saturation, controller_error, joint_limit_violation, trajectory_deviation | quadrotor, ur5 | ✅ |
| generic_time_budget | task_timeout | ur5 |   |
| warm_start_from_prior_best | trajectory_deviation | ur5 |   |

## Acceptance gates

- ❌  ≥1 FailureMode seen on ≥2 embodiments
- ✅  ≥1 pattern transferable across ≥2 embodiments
