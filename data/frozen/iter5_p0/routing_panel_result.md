# Routing Panel Report

- **HOW base**: `http://127.0.0.1:8088`
- **router_backend**: `seekdb`
- **healthz status**: `ok`
- **panel**: `18` tasks

## Metrics

| Metric | Value |
|--------|-------|
| Accuracy (positive) | 100.00% |
| Adversarial false-positive rate | 0.00% |
| Collateral false-injection rate | 0.00% |
| Overall false-injection rate | 0.00% |
| Passed | 18 / 18 |

## Results

| task_id | type | status | strategy | pattern_id | similarity |
|---------|------|--------|----------|------------|------------|
| TASK_001_PIDTuning | positive | pass | CATALYST | pid_joint_latency_oscillation | 0.7198 |
| TASK_002_QuadrupedGait | positive | pass | CATALYST | terrain_aware_locomotion | 0.8389 |
| TASK_003_RobotArmCycleTime | positive | pass | CATALYST | time_optimal_path_blending | 0.7390 |
| TASK_005_AES128_Throughput | positive | pass | CATALYST | simd_aes_ni_hardware_crypto | 0.6263 |
| TASK_006_FlashAttention | positive | pass | CATALYST | flash_attention_tiled_softmax | 0.8203 |
| TASK_007_BatteryFastCharging | positive | pass | CATALYST | multi_stage_cc_cv_fast_charging | 0.6534 |
| TASK_008_JobShop_abz | positive | pass | CATALYST | metaheuristic_combinatorial_escape | 0.6145 |
| TASK_010_UAVInspection | positive | pass | CATALYST | motion_blur_imu_aided_deblur | 0.7180 |
| TASK_W_001_KVCacheLongContext | positive | pass | CATALYST | sliding_window_kv_cache | 0.6434 |
| TASK_W_002_GradExplosionRL | positive | pass | SAFETY | — | — |
| TASK_W_003_NetRetryStorm | positive | pass | CATALYST | exponential_backoff_retry | 0.7240 |
| TASK_W_004_EntropyCollapsePPO | positive | pass | CATALYST | ppo_entropy_collapse_guard | 0.6636 |
| TASK_W_005_ActuatorOvershoot | positive | pass | CATALYST | output_saturation_clamp | 0.7631 |
| TASK_W_006_PlanningDivergence | positive | pass | CATALYST | closed_loop_replanning | 0.7043 |
| TASK_W_007_IntegrationWindup | positive | pass | CATALYST | anti_windup_pid | 0.7969 |
| TASK_W_008_AttentionMemoryOOM | positive | pass | CATALYST | flash_attention_tiled_softmax | 0.6052 |
| TASK_004_HighReliableSimulation | adversarial | pass | ABSTAIN | — | — |
| TASK_009_TopologyOptimization | adversarial | pass | ABSTAIN | — | — |

## Decision

ALL PASS — routing panel cleared.