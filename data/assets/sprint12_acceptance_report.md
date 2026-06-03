# Sprint 12 — bridge_reweighter direct path

**Goal:** Sprint 11 wired real-robot evidence into Sprint 6's evidence
loop via an intermediate `evidence_stats.json` file.  Sprint 12 closes
that gap so callers can drive the full pipeline in memory.

## What changed

| Layer | Before (Sprint 11) | After (Sprint 12) |
|---|---|---|
| RobotEvent → bridge_index | 3 disk hops (RobotEvent JSONL → EvidenceTrace JSONL → evidence_stats.json → bridge_index.json) | **1 function call**, only bridge_index.json on disk |
| `bridge_reweighter` public API | `reweight_bridge_index(bridge_path, metrics_path, evidence_stats_path)` only | `+ reweight_bridge_index_from_stats(stats, *, bridge_path, metrics_path)` `+ reweight_bridge_index_from_traces(traces, *, bridge_path, metrics_path)` |
| `sim_ingest` namespace | `read_robot_event_jsonl`, `events_to_evidence_traces`, … | `+ reweight_bridge_from_robot_events(events, *, bridge_path, metrics_path)` |

## End-to-end demo

```python
from rosclaw_know.sim_ingest import (
    read_robot_event_jsonl,
    reweight_bridge_from_robot_events,
)

events = read_robot_event_jsonl("logs/today.rosbag.jsonl")
summary, coverage = reweight_bridge_from_robot_events(
    events,
    bridge_path=Path("data/assets/bridge_index.json"),
)
# bridge_index.json is now updated in place; no intermediate files.
```

Run on `tests/fixtures/sprint11/robot_traces_with_evidence.jsonl`:

```
summary  : {clusters_touched: 5, clusters_promoted: 1, mode: "v2"}
coverage : violations=[]
cluster  : windup → priority=1, uplift_mean=0.272, placebo_adjusted_uplift=0.224
```

## Acceptance gates

| # | Gate | Threshold | Actual | Status |
|---|---|---|---|:---:|
| 1 | `reweight_bridge_index_from_stats` accepts in-memory `dict[str, EvidenceStat]` | required | yes | ✅ |
| 2 | `reweight_bridge_index_from_traces` runs distill + reweight in one call | required | yes | ✅ |
| 3 | `reweight_bridge_from_robot_events` runs Sprint 9 → Sprint 11 → Sprint 6 → Sprint 12 in one call | required | yes | ✅ |
| 4 | **Byte-for-byte parity** with the disk path (Sprint 11 + reweight_bridge_index) | required | yes (`test_direct_path_matches_disk_path_byte_for_byte`) | ✅ |
| 5 | Promotes when placebo_adjusted_uplift > threshold | required | yes (priority=1) | ✅ |
| 6 | Demotes when true arm underperforms placebo | required | yes (priority=-1) | ✅ |
| 7 | Missing bridge_index.json → noop, no crash | required | yes | ✅ |
| 8 | Empty traces input → noop | required | yes | ✅ |
| 9 | Unrelated clusters unaffected | required | yes | ✅ |
| 10 | CoverageReport returned alongside summary | required | yes (Sprint 6's coverage gates surfaced) | ✅ |

## Why this matters

After Sprint 12, the **complete** real-robot self-improvement loop runs
without touching disk for intermediates:

```
real-robot rollout
  ─► RobotEvent (in-memory)
     ─► EvidenceTrace (in-memory)
        ─► EvidenceStat (in-memory)
           ─► bridge_index.json  (ONLY disk hop, where it has to be)
```

This is what production deployments will want: a robot finishes a
task, emits a RobotEvent stream, and the catalog is reweighted before
the next task starts — no batch jobs, no temp files, no cron.

## Files touched

- `src/rosclaw_know/bridge_reweighter.py` — added
  `reweight_bridge_index_from_stats(stats, *, bridge_path, metrics_path)`
  and `reweight_bridge_index_from_traces(traces, *, bridge_path,
  metrics_path)`.  Both reuse the existing internal
  `_reweight_with_evidence_v2` so the v2 promotion/demotion logic stays
  in one place.
- `src/rosclaw_know/sim_ingest/bridge_direct.py` (new) —
  `reweight_bridge_from_robot_events(events, *, bridge_path,
  metrics_path)`.
- `src/rosclaw_know/sim_ingest/__init__.py` — export the new one-liner.
- `tests/test_sprint12_bridge_direct.py` (new, 10 cases) — pure
  function contracts + byte-for-byte parity test + missing-file
  graceful noop + demote / promote / no-op invariants.
- `data/assets/sprint12_acceptance_report.{json,md}` — this file.

## Tests

```
pytest -q tests/test_sprint12_bridge_direct.py  → 10 passed
pytest -q (full suite)                          → 458 passed
ruff check src/.../bridge_reweighter.py ...     → All checks passed!
```
