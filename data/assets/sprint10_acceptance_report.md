# Sprint 10 — Auto-Derived Cross-Embodiment Transfer Table

**Goal:** replace the Sprint 9 hand-curated `PATTERN_TRANSFER_TABLE`
with a pure function that mines the same mapping from
`failure_taxonomy.yaml` ∪ `physical_graph.json` (FixPattern.failure_ids).

## What changed

| Component | Before (Sprint 9) | After (Sprint 10) |
|---|---|---|
| `cross_embodiment.PATTERN_TRANSFER_TABLE` | 8 hand-curated rows | **removed** |
| `_EVENT_TYPE_ALIASES` (vocabulary) | n/a | 8 event_types × 4–9 token aliases (taxonomic only — no pattern_ids) |
| `derive_pattern_transfer_table(failures, fix_patterns)` | n/a | pure function: structural join of catalog FailureMode × FixPattern.failure_ids |
| `load_default_transfer_table()` | n/a | reads `data/assets/physical_graph.json` + `failure_taxonomy.yaml` |
| `run_cross_embodiment_check(..., transfer_table=None)` | dispatched to constant | dispatches to `load_default_transfer_table` by default |

The phantom names that Sprint 9 emitted (`anti_windup`,
`controller_output_clamp` without `compiled_` prefix) no longer exist
anywhere — every `pattern_id` in the report is a real catalog ID.

## Acceptance proof

```json
{
  "auto_derived_transfer_table": {
    "actuator_saturation": [
      "compiled_controller_output_clamp",
      "compiled_zero_integral_gain_on_saturation"
    ],
    "controller_error": [
      "compiled_zero_integral_gain_on_saturation"
    ],
    "task_timeout": [
      "compiled_add_time_budget",
      "compiled_generic_time_budget",
      "compiled_warm_start_from_prior_best"
    ]
  },
  "distinct_event_types": 3,
  "distinct_patterns": 5,
  "rules_emitted_from_data": 6,
  "phantom_pattern_names_present": false
}
```

### Sprint 9 fixture re-run

Same `tests/fixtures/sprint9/` rosbag + Isaac + MuJoCo + Foxglove input
now produces:

```
- patterns on ≥2 embodiments: 1
- compiled_zero_integral_gain_on_saturation  ur5, quadrotor  ✅
```

Plan §Sprint 9 main acceptance gate (pattern transferable across ≥2
embodiments) **still passes**. The number of cross-embodiment patterns
dropped from 3 (Sprint 9) to 1 (Sprint 10) because Sprint 9's bonus
rows were false positives from the AES/CUDA bucket — those don't
actually fix actuator_saturation failures, the hand-curated table just
matched the word "saturation".

## Acceptance gates

| # | Gate | Threshold | Actual | Status |
|---|---|---|---|:---:|
| 1 | Hand-curated `PATTERN_TRANSFER_TABLE` removed from module | required | gone | ✅ |
| 2 | Default transfer table loadable from catalog | required | yes | ✅ |
| 3 | Distinct event_types covered | ≥ 3 | 3 | ✅ |
| 4 | No phantom pattern names | 0 | 0 | ✅ |
| 5 | Every emitted pattern_id ∈ graph FixPattern set | required | yes | ✅ |
| 6 | Sprint 9 fixture still passes §Sprint 9 gate | required | yes | ✅ |
| 7 | Pure function: deterministic + sorted output | required | yes | ✅ |
| 8 | Empty-table injection drops all pattern rows | required | yes | ✅ |

## Why three event_types and not eight

Coverage is gated by **how many catalog FailureModes (a) match an
event_type and (b) have a FixPattern targeting them**.  Failures
without fixes in the graph today:

- `failure_planning_divergence` (would feed `trajectory_deviation`)
- `failure_actuator_output_unbounded` (would re-feed `actuator_saturation`)
- `failure_cuda_launch_overhead`, `failure_gradient_explosion`,
  `failure_kv_cache_unbounded_growth`, `failure_ppo_entropy_collapse`,
  `failure_simulator_compile_failure` (software failures, no robot
  event_type)

Sprint 11 (self-improvement) extends coverage by mining
`EvidenceTrace`s from real-robot ingest and pumping them through
`bridge_reweighter` so that pattern↔failure links emerge from data
instead of the hand-curated `_MUTATION_KIND_TO_FAILURE` fallback in
`build_physical_graph.py`.

## Files touched

- `src/rosclaw_know/sim_ingest/cross_embodiment.py` — full rewrite
- `src/rosclaw_know/sim_ingest/__init__.py` — drop `PATTERN_TRANSFER_TABLE`, add `derive_pattern_transfer_table` + `load_default_transfer_table`
- `tests/test_cross_embodiment_auto.py` — new, 16 cases
- `tests/test_sim_ingest_cross_embodiment.py` — updated to assert real catalog IDs (not phantoms)
- `data/assets/sprint9_ingest_reference.json` / `.md` — regenerated
- `data/assets/sprint10_acceptance_report.json` / `.md` — this file

## Tests

```
pytest -q tests/test_cross_embodiment_auto.py     →  16 passed
pytest -q tests/test_sim_ingest_cross_embodiment.py →  12 passed
pytest -q (full suite)                             → 426 passed
```
