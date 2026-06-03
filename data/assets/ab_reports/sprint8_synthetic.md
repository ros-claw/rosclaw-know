# Sprint 8 — 6-arm A/B harness summary

**Tasks**: 10    **Arms**: 6    **Seeds**: 3    **Trials**: 180

## Per-arm aggregates

| arm | avg_rank ↓ | win_vs_baseline | Δ_post_injection | validity | mean_hint_use |
|---|---:|---:|---:|---:|---:|
| `baseline` | 4.800 | 0% | +0.0000 | 100% | 0% |
| `true_know` | 2.000 | 100% | +0.1464 | 100% | 80% |
| `placebo_know` | 4.400 | 70% | +0.0085 | 93% | 0% |
| `shuffled_know` | 5.800 | 10% | -0.0305 | 100% | 10% |
| `task_pack_only` | 3.000 | 100% | +0.0885 | 97% | 43% |
| `task_pack_plus_catalyst` | 1.000 | 100% | +0.1923 | 100% | 80% |

## Acceptance gates

- ✅ **true_know_beats_placebo** — True_Know.avg_rank < Placebo_Know.avg_rank.  true_know.avg_rank=2.0 vs placebo_know.avg_rank=4.4
- ✅ **true_know_beats_shuffled** — True_Know.avg_rank < Shuffled_Know.avg_rank.  true_know.avg_rank=2.0 vs shuffled_know.avg_rank=5.8
- ✅ **pack_plus_catalyst_beats_baseline** — TaskPack+CATALYST.avg_rank < Baseline.avg_rank.  pack+catalyst.avg_rank=1.0 vs baseline.avg_rank=4.8
- ✅ **positive_delta_majority** — ≥6/10 tasks have positive True_Know vs Baseline delta.  10/10 positive
- ✅ **significant_trend_count** — ≥4/10 tasks reach p<0.1 with positive delta.  significant tasks: ['battery_fast_charging', 'crypto_aes128', 'flash_attention', 'high_reliable_simulation', 'jobshop_abz', 'pid_tuning', 'quadruped_gait', 'robot_arm_cycle_time', 'topology_optimization', 'uav_inspection']

**Verdict**: 5/5 gates passed
