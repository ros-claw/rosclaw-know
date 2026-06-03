# Sprint 11 — Self-improvement loop: real-robot evidence ↔ v1.5 catalog

**Goal:** close the loop the user named #4 — "Sprint 9 真机 trace 喂回
Sprint 3 / Sprint 6, 让 pattern 越用越精."  Two paths:

1. **Promotion** — real-robot rollouts on a *known* catalog pattern feed
   `evidence_distill` → `bridge_reweighter`, so the v2 catalog learns
   what actually works in the field.
2. **Discovery** — real-robot rollouts using an *unknown* pattern_id
   that beats placebo are emitted as `CandidatePattern` entries, ready
   for Sprint 4's pattern compiler to graduate into the catalog.

## Pipeline

```
rosbag / Isaac / MuJoCo / Foxglove
  ─────► RobotEvent JSONL  (carrying task_run envelopes)
            │
            │  read_robot_event_jsonl
            ▼
       list[RobotEvent]
            │
            │  events_to_evidence_traces
            ▼
      list[EvidenceTrace]
            ├──────────────────────────────┐
            ▼                              ▼
   evidence_distill (Sprint 6)   extract_candidates_from_evidence_traces
            │                              │
            ▼                              ▼
   PROMOTE / DEMOTE verdict           CandidatePattern (Sprint 4-ready)
            │
            ▼
   bridge_reweighter → updated bridge_index
```

## New surface (Sprint 11)

| Symbol | Module | Purpose |
|---|---|---|
| `read_robot_event_jsonl(path)` | `sim_ingest.event_to_evidence` | parse RobotEvent JSONL → list[RobotEvent] |
| `events_to_evidence_traces(events)` | same | batch convert; drops events without task_run |
| `extract_candidates_from_evidence_traces(...)` | `sim_ingest.robot_trajectory_extractor` | pure func: traces → CandidatePattern[] (discovery only) |
| `scripts/ingest_robot_evidence.py` | scripts | CLI: real-robot JSONL → `evidence_stats.json` |

## Acceptance proof

### Promotion path (`tests/fixtures/sprint11/robot_traces_with_evidence.jsonl`)

10 RobotEvents → 10 EvidenceTraces (5 true / 5 placebo) of the same
catalog pattern `compiled_zero_integral_gain_on_saturation` running on
`ur5` + `quadrotor`.

| field | value |
|---|---|
| `n_by_arm` | true=5, placebo=5 |
| `placebo_adjusted_uplift` | **+0.224** (>= 0.03 threshold) |
| `is_promoted(stat)` | **True** |
| coverage gates | clean (0 violations) |

Plan §Sprint 6's `MIN_SAMPLE_SIZE=5` and `ADJUSTED_PROMOTE_THRESHOLD=0.03`
both cleared — promotion verdict is real-robot-driven.

### Discovery path (`tests/fixtures/sprint11/discovery_traces.jsonl`)

6 RobotEvents with an *unknown* pattern_id `novel_torque_feedback_loop`
on task `stack_blocks_with_torque_feedback` (3 true / 3 placebo).

| field | value |
|---|---|
| candidate emitted | `candidate_real_robot_novel_torque_feedback_loop` |
| `task_family` | `robotics_optimization` |
| `evidence_count` | 6 |
| `avg_score_delta` | **+0.283** |
| `successful_mutations[0].kind` | `other` (real-robot diff is unstructured) |
| `source_trajectory_ids` | 6 entries, one per fixture trace |

The discovered candidate is now usable by Sprint 4's pattern compiler.

## Why this matters

v1.5 sprints 0-10 mined patterns from **offline** Frontier-Eng baseline
archives.  Sprint 11 lets the *same* knowledge plumbing learn from
**online** robot data, so the catalog gets denser over time without
re-running offline mining.  Same threshold (`ADJUSTED_PROMOTE_THRESHOLD`)
governs promotion in both paths — they agree on what counts as a real
improvement.

## Acceptance gates

| # | Gate | Threshold | Actual | Status |
|---|---|---|---|:---:|
| 1 | Sprint 9 RobotEvent → EvidenceTrace path | required | wired | ✅ |
| 2 | Real-robot fixture promotes a catalog pattern | required | yes (compiled_zero_integral_gain_on_saturation) | ✅ |
| 3 | Real-robot fixture promotes via placebo-adjusted uplift, not raw | required | yes (raw=0.16, adj=0.224 — both checked) | ✅ |
| 4 | Discovery path emits ≥1 new CandidatePattern from real-robot | ≥ 1 | 1 (candidate_real_robot_novel_torque_feedback_loop) | ✅ |
| 5 | CandidatePattern uses MutationKind="other" (unstructured) | required | yes | ✅ |
| 6 | source_trajectory_ids cites every contributing trace | required | yes (6/6) | ✅ |
| 7 | CLI script (scripts/ingest_robot_evidence.py) runs end-to-end | required | yes | ✅ |
| 8 | Same threshold governs promotion + discovery | required | both use ADJUSTED_PROMOTE_THRESHOLD | ✅ |
| 9 | Known catalog patterns NOT re-discovered | required | known_pattern_ids filter dropped them | ✅ |
| 10 | Negative control: equal-arm fixture does NOT promote | required | yes (test_no_promotion_when_placebo_matches_true) | ✅ |
| 11 | Demotion: true-arm underperforms placebo → demote | required | yes (test_demotion_when_true_underperforms_placebo) | ✅ |

## Files touched

- `src/rosclaw_know/sim_ingest/event_to_evidence.py` — added
  `read_robot_event_jsonl` + `events_to_evidence_traces` batch helpers
- `src/rosclaw_know/sim_ingest/robot_trajectory_extractor.py` — new
  discovery extractor (`extract_candidates_from_evidence_traces`)
- `src/rosclaw_know/sim_ingest/__init__.py` — export new symbols
- `src/rosclaw_know/sim_ingest/cross_embodiment.py` — drop lru_cache
  from `load_default_transfer_table` (Sprint 10 cleanup; cache was
  papering over a test-pipeline cfg leak)
- `tests/fixtures/sprint11/robot_traces_with_evidence.jsonl` — promotion fixture
- `tests/fixtures/sprint11/discovery_traces.jsonl` — discovery fixture
- `tests/test_sprint11_robot_evidence_loop.py` — 12 cases
- `tests/test_sprint11_robot_trajectory_extractor.py` — 10 cases
- `tests/test_pipeline.py` — restore cfg in tearDown (was leaking)
- `scripts/ingest_robot_evidence.py` — CLI wrapper
- `data/assets/sprint11_acceptance_report.{json,md}` — this file

## Tests

```
pytest -q tests/test_sprint11_robot_evidence_loop.py          → 12 passed
pytest -q tests/test_sprint11_robot_trajectory_extractor.py   → 10 passed
pytest -q (full suite)                                        → 448 passed
```
