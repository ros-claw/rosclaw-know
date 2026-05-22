# ROSClaw-Know

> **v0.8.1 · Phase 1–8 closed · 349 clusters · 2026-05-20**
> Canonical state: [`docs/ROADMAP.md`](docs/ROADMAP.md) · runtime stats:
> [`data/assets/_runtime_stats.json`](data/assets/_runtime_stats.json) ·
> AI agents start at [`AGENTS.md`](AGENTS.md)

**Offline knowledge refinery** for the ROSClaw embodied-intelligence stack.

Converts free-form robotics wiki pages (paper abstracts, design notes, code
fragments) into **procedural knowledge** — symptom → fix_pattern pairs with
cross-domain analogies — that runtime agents act on through the sister
project [`rosclaw-how`](../rosclaw-how).

Phase 1–8 are closed. The system is a self-improving knowledge engine: new
sources flow in via `scripts/ingest.py` (or `scripts/ingest_awesome.py` for
curated GitHub lists), agent feedback flows back via
`scripts/distill_feedback.py`, and cold-spots auto-draft patch sources via
`scripts/autodraft.py`. The full loop is verified end-to-end by
`scripts/verify_phase7_active.py` (6/6 PASS) and
`scripts/verify_phase8_awesome.py` (2/2 PASS, control-theory + ICS).

## What's in the box

| Module / script | Phase | Purpose |
|---|---|---|
| `pipeline.py` + `run_phase1.py` | 1 | wiki → harvester → weaver → Muse → curated publish |
| `feedback_distill.py` + `distill_feedback.py` | 4 | outcomes → per-pattern uplift / win-rate / last_seen |
| `bridge_reweighter.py` + `reweight_bridge.py` | 4 | n-weighted merge of metrics back into `bridge_index.json` |
| `source_manifest.py` + `incremental_pipeline.py` + `ingest.py` | 5 | content-hash dirty detection, selective Muse, non-destructive merge |
| `lint_bridge.py` | 5 | orphan / missing / dup / stale-demotion lint |
| `stats_analyze.py` + `analyze_stats.py` | 6 | snapshot → linear-regression trend → markdown report |
| `bench_phase6.py` | 6 | p50/p95 SLO benchmark (build / feedback / reload / export) |
| `active_learning.py` + `autodraft.py` | 7 | poll `/blind_spots` → DeepSeek draft → auto-ingest |
| `promote.py` | 7 | staging maturation gate (n≥5 + uplift > ±0.05 → priority ±1) |
| `verify_phase7_active.py` | 7 | 8-step end-to-end joint verify with rosclaw-how |
| `awesome_fetcher.py` + `ingest_awesome.py` | 8 | pull curated GitHub awesome lists (markdown OR HTML-table format), download referenced content, write to wiki/awesome_corpus/ as priority=0 staging |
| `verify_phase8_awesome.py` | 8 | end-to-end verify: fetch awesome list → ingest → reload → CATALYST hit on new staging cluster |

## Quick start

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure
cp .env.example .env
# edit .env: set DEEPSEEK_API_KEY (or ROSCLAW_KNOW_MOCK_LLM=1 for dry runs)

# 3. First-time mine (small batch first — 200 pages, ~2 RMB, ~10 min)
python scripts/run_phase1.py --max-pages 200

# 4. Audit a sample
python scripts/inspect_samples.py --n 30

# 5. Full run (after audit passes ≥85%)
python scripts/run_phase1.py

# 6. Ingest a new paper without re-mining the whole corpus
python scripts/ingest.py path/to/new_paper.md

# 7. After production traffic accrues, distill + reweight
python scripts/distill_feedback.py --summary
python scripts/reweight_bridge.py

# 8. Auto-draft for cold-spots (requires rosclaw-how live on :47820)
python scripts/autodraft.py --then-ingest

# 9. Promote staging clusters with positive feedback
python scripts/promote.py --apply

# 10. Bulk ingest from a curated awesome list (Phase 8)
python scripts/ingest_awesome.py \
    --url https://github.com/A-make/awesome-control-theory \
    --then-ingest
```

## Architecture (Phase 1–7)

```
                   ┌──────────────┐
   wiki/*.md ────▶ │  harvester   │ ──▶ extracted_pages
                   └──────────────┘                 │
                                                    ▼
                   ┌──────────────┐         ┌───────────────┐
   source_manifest │   weaver     │ ──────▶ │ NetworkX graph│
   tracks dirty    │              │         │  (in-memory)  │
   files only      └──────────────┘         └───────────────┘
   (Phase 5)                                       │
                                                    ▼
                   ┌──────────────────┐
                   │  Muse compiler   │ ──▶ bridge_index.json
                   │  (LLM analogies) │     code_patterns/*.md
                   └──────────────────┘            │
                          ▲                        │
                          │     (Phase 7 staging)  │
                          │                        ▼
                          │            ┌────────────────────┐
                          │            │     rosclaw-how    │
                          │            │  SeekDB hot path   │
                          │            └────────────────────┘
                          │                        │
                          │  Phase 4 distill       │
                          ├────────────────────────┤
                          │  outcomes-*.jsonl      │
                          │  pattern_metrics.json  │
                          │                        │
                          │  Phase 7 autodraft     │
                          ├────────────────────────┤
                          │  /blind_spots          │
                          │  → DeepSeek            │
                          │  → wiki/auto_drafted/  │
                          └────────────────────────┘
```

## Lifecycle (Phase 7 staging maturation)

```
            ┌──────────┐  uplift > +0.05  ┌────────────┐
ingest ───▶ │ staging  │ ────────────────▶│ production │
            │ priority │                  │ priority+1 │
            │   = 0    │                  └────────────┘
            └──────────┘                        │
                  │   uplift < -0.05            │ uplift < -0.05
                  │                             ▼
                  ▼                       ┌────────────┐
            ┌──────────┐                  │  demoted   │
            │ demoted  │ ◀────────────────│ priority−1 │
            │ skipped  │                  │ runtime    │
            │ in route │                  │  skips it  │
            └──────────┘                  └────────────┘
```

Lifecycle transitions are driven by `scripts/promote.py` which calls
`POST /wiki/v1/admin/promote` on rosclaw-how. The bridge stores `priority`
inline; rosclaw-how's asset_loader pushes only `priority ≥ 0` clusters into
the live SeekDB collection.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
# 63 / 64 pass (one pre-existing test_pipeline mock-LLM stub)
```

Test coverage:

- `test_feedback_distill.py` — 11 tests, Phase 4 distill logic
- `test_bridge_reweighter.py` — 6 tests, n-weighted merge + demotion gating
- `test_source_manifest.py` — 9 tests, content-hash dirty detection
- `test_incremental_pipeline.py` — 5 tests, non-destructive merge
- `test_lint_bridge.py` — 11 tests, orphan / missing / dup detection
- `test_stats_analyze.py` — 14 tests, trend regression + classification
- `test_active_learning.py` — 6 tests, autodraft + blind-spot adapter

## Joint verification (with rosclaw-how)

```bash
# Bring rosclaw-how up first
cd ../rosclaw-how
ROSCLAW_HOW_ROUTER_BACKEND=seekdb \
SEEKDB_DATABASE=rosclaw_how SEEKDB_TENANT=mysql \
.venv/bin/python scripts/run_server.py &

# Then from rosclaw-know
python scripts/replay_benchmark.py         # Phase 4 — 60-rollout uplift A/B
python scripts/verify_phase5_ingest.py     # Phase 5 — ingest + hot-reload round-trip
python scripts/bench_phase6.py             # Phase 6 — SLO benchmark
python scripts/verify_phase7_active.py     # Phase 7 — end-to-end self-improvement
```

Latest verified results (`data/benchmarks/`):

- **Phase 4 replay**: 6/6 patterns correctly classified, 3 soft-deprecated
- **Phase 5 ingest**: PASS — new cluster routable in <1 s after reload
- **Phase 6 perf**: ALL SLOs MET — build p95 < 400 ms, reload **284 ms delta**
  (398× faster than full re-encode), feedback p95 < 35 ms
- **Phase 7 active**: PASS — autodrafted cluster (sim 0.657) promoted to
  production after 5 positive feedbacks, final /build silently injected
  with `is_staging` falsy
- **Phase 8 awesome**: PASS — 47 corpus files from
  `A-make/awesome-control-theory` + `hslatman/awesome-ics-security`
  → 16 new staging clusters (sim 0.52–0.82 on PID / MPC / ICS probes)

## What this replaces

The legacy `rosclaw-wiki` project has been retired:

- 6,097 markdown pages → raw input to this pipeline (symlinked from `wiki/`)
- Online endpoints → reborn as `rosclaw-how`

See [`../rosclaw-how/README.md`](../rosclaw-how/README.md) for the runtime
side.
