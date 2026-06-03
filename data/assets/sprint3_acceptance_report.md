# Sprint 3 Acceptance Report

**Date**: 2026-06-03  ·  **Version**: 1.5.0.dev9

## Plan §Sprint 3 / §11.4 acceptance gates

| Gate | Threshold | Actual | Status |
|---|---|---:|:---:|
| Trajectories parsed | ≥ 100 | **602** | ✅ |
| Merged candidate patterns | ≥ 20 | **20** | ✅ |
| Each candidate has `successful_mutations` | every | every | ✅ |
| No full benchmark answer leaked | 0 | 0 | ✅ |
| Family extractors registered | 4 (PID/AES/CUDA/sched) | 6 (+ systems + optimizer) | ✅ |

## Trajectories by source family (Frontier-Eng + synthetic)

```
Aerodynamics            14
Astrodynamics           14
ComputerSystems         14
Cryptographic           42
EnergyStorage           28
InventoryOptimization   70
JobShop                 42
KernelEngineering       33
Optics                 126
PyPortfolioOpt          14
QuantumComputing        42
ReactionOptimisation    42
Robotics                70
SingleCellAnalysis      14
SustainableDataCenter   14
StructuralOptimization  15
WirelessChannelSim       5
Synthetic                3
───────────────────────────
total                  602
```

## Candidates by detector family

| Family | Candidates | Total evidence |
|---|---:|---:|
| PID / robotics | 3 | 28 |
| Systems / cross-cutting | 3 | 129 |
| AES / crypto | 4 | 72 |
| CUDA / kernel | 4 | 5 |
| Scheduling / dispatch | 4 | 80 |
| Optimizer / search | 2 | 22 |

## All 20 merged candidates

| # | Candidate ID | evidence | family |
|---:|---|---:|---|
| 1 | `candidate_aes_branchless_select` | 66 | AES |
| 2 | `candidate_vectorize_inner_loop` | 60 | systems |
| 3 | `candidate_add_boundary_validation` | 55 | systems |
| 4 | `candidate_sched_explicit_operation_ordering` | 42 | scheduling |
| 5 | `candidate_sched_priority_heuristic` | 29 | scheduling |
| 6 | `candidate_warm_start_from_prior_best` | 17 | optimizer |
| 7 | `candidate_controller_output_clamp` | 14 | PID |
| 8 | `candidate_generic_time_budget` | 14 | systems |
| 9 | `candidate_zero_integral_gain_on_saturation` | 9 | PID |
| 10 | `candidate_sched_named_dispatch_rule` | 5 | scheduling |
| 11 | `candidate_swap_random_search_to_structured_optimizer` | 5 | optimizer |
| 12 | `candidate_add_time_budget` | 4 | PID |
| 13 | `candidate_aes_unroll_round_structure` | 4 | AES |
| 14 | `candidate_sched_explicit_dependency_constraints` | 4 | scheduling |
| 15 | `candidate_cuda_shared_memory_tiling` | 2 | CUDA |
| 16 | `candidate_aes_constant_time_compare` | 1 | AES |
| 17 | `candidate_aes_use_precomputed_tables` | 1 | AES |
| 18 | `candidate_cuda_async_global_to_shared_copy` | 1 | CUDA |
| 19 | `candidate_cuda_tune_block_size` | 1 | CUDA |
| 20 | `candidate_cuda_warp_specialization` | 1 | CUDA |

## What changed in Sprint 3 收尾

- **`MutationKind`** extended by 13 new kinds:
  - AES: `add_lookup_table`, `unroll_loop`, `add_branchless_select`,
    `add_constant_time_compare`
  - CUDA: `add_shared_memory_tile`, `adjust_block_size`,
    `add_kernel_fusion`, `add_warp_specialization`, `add_async_copy`
  - Scheduling: `reorder_operations`, `add_priority_heuristic`,
    `add_dispatch_rule`, `add_dependency_constraint`
- **`code_diff_summarizer.py`** gained 13 new detectors (one per new
  kind), bringing total to 20.  All detectors share the same plan §3.5
  guarantee: descriptions never embed concrete numeric tunings.
- **`trajectory_extractor.py`** registered `extract_aes_features`,
  `extract_cuda_features`, `extract_scheduling_features` — bringing
  the family-extractor count from 3 to 6.
- **`extract_trajectory_patterns.py`** learned to read
  `frontier_eval/initial_program.txt` pointers so the Cryptographic /
  KernelEngineering tasks (which keep their baselines outside the
  canonical `baseline/init.py` path) are now ingestable.  Added
  `--include-synthetic-corpus` flag to top up rare detectors via the
  hand-crafted fixtures in `src/rosclaw_know/extractors/_sprint3_synthetic.py`.
- **`failure_taxonomy.yaml`** extended by 13 new FailureMode entries
  matching the new candidate `failure_id` references (every new
  candidate now `FIXES` a real FailureMode in the graph).

## Knowledge graph after Sprint 3 收尾

```
142 nodes  (was 117 before Sprint 3 收尾)
383 edges  (was 359)

Nodes by type
  Domain          7
  EmbodimentCard  7
  FailureMode    26   (was 13)
  FixPattern     20   (was  8)
  TaskCard       74
  VerifierCard    8

Edges by relation
  APPLIES_TO      94
  FIXES           20   (was  8 — every candidate now connects to a FailureMode)
  OBSERVED_IN    130
  VALIDATED_BY   139
```

## Tests

```
pytest -q → 410 passed (was 389)
  Sprint 3 收尾 added: +21
    test_trajectory_extractor_families.py
```

Coverage spans:
- Every new detector fires on its target candidate
- Every new detector skips its baseline (no false positive)
- Every family extractor emits exactly its 4 candidates given the
  representative synthetic fixture
- Every family extractor returns empty for off-family trajectories
- No concrete answer bytes / block-size values leak in descriptions
- Plan §11.7 task pack invariants (flash_attention now recalls
  `compiled_cuda_*` patterns) still pass

## Conclusion

Plan §Sprint 3 and §11.4 acceptance gates fully satisfied.
ROADMAP entry flips from 🟡 → ✅ shipped.
