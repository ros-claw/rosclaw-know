# rosclaw-know — Deploy Guide

Companion to [`ROADMAP.md`](ROADMAP.md). Covers the operator side: how to
install, when to mine, when to ingest incrementally, how to back up, and
how the data flows to rosclaw-how.

> rosclaw-know is the **offline** half of the stack. It never serves
> agents directly — it produces `data/assets/bridge_index.json` and
> `data/assets/code_patterns/*.md`. rosclaw-how reads those at start-up
> and again on every `POST /admin/reload`. See
> [`../rosclaw-how/docs/DEPLOY.md`](../../rosclaw-how/docs/DEPLOY.md) for
> the runtime side.

---

## Environment

| | Recommended | Hard requirement |
|---|---|---|
| Python | 3.11 | 3.10+ |
| RAM | 4 GB | 2 GB (with `--no-muse` re-mines) |
| Disk | 10 GB free | 2 GB free (corpus + bridge + venv) |
| Network | outbound HTTPS to `api.deepseek.com`, GitHub | only for first-time mine + autodraft |

`pylibseekdb` is **not** required for rosclaw-know — it's only needed by
rosclaw-how. rosclaw-know talks to rosclaw-how over HTTP.

## First-time install

```bash
git clone <this-repo>
cd rosclaw-know
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env:
#   DEEPSEEK_API_KEY=sk-...
#   DEEPSEEK_BASE_URL=https://api.deepseek.com
#   DEEPSEEK_EXTRACTOR_MODEL=deepseek-chat
#   DEEPSEEK_MUSE_MODEL=deepseek-chat
#   WIKI_DIR=wiki       # default; symlink or rsync your sources in
```

Smoke-test without API key:

```bash
ROSCLAW_KNOW_MOCK_LLM=1 python -m unittest discover -s tests -p "test_*.py"
# expect 77/78 PASS (one pre-existing test_pipeline mock stub)
```

## Workflows

### A. Cold-start mine (first deploy or full refresh)

```bash
# Small batch — audit before going wide
python scripts/run_phase1.py --max-pages 200
python scripts/inspect_samples.py --n 30

# Full run (cost depends on corpus size; ~0.6 RMB for the legacy 6 k pages)
python scripts/run_phase1.py
```

Outputs `data/assets/bridge_index.json` (~150 KB at 80 clusters) and
`data/assets/code_patterns/*.md`. Trigger rosclaw-how to load:

```bash
curl -X POST -H "X-API-Key: ..." http://rosclaw-how:8088/wiki/v1/admin/reload
```

### B. Incremental ingest (you have new papers / articles)

```bash
# A single new paper
python scripts/ingest.py path/to/new_paper.md

# A directory of new sources
python scripts/ingest.py path/to/new_corpus/

# Dry-run first to see what will be processed
python scripts/ingest.py path/to/new_corpus/ --dry-run
```

`source_manifest.json` tracks SHA-256 of every processed file; only dirty
(new or content-changed) files run through harvester + Muse. Existing
Phase 4 metrics on clusters are preserved.

After ingest:
```bash
curl -X POST -H "X-API-Key: ..." http://rosclaw-how:8088/wiki/v1/admin/reload
```

### C. Awesome-list bulk pull (Phase 8)

```bash
python scripts/ingest_awesome.py \
    --url https://github.com/A-make/awesome-control-theory \
    --then-ingest

# Targeted section pull
python scripts/ingest_awesome.py \
    --url https://github.com/hslatman/awesome-industrial-control-system-security \
    --section literature --section tools \
    --limit 30 --then-ingest
```

Corpus lands in `wiki/awesome_corpus/<list_slug>/`. The `--then-ingest`
flag chains `scripts/ingest.py` so new clusters appear in `bridge_index`
in one pass. Reload rosclaw-how afterward.

### D. Feedback distillation (run after production traffic accrues)

```bash
# Pull outcomes export from rosclaw-how
curl -H "X-API-Key: ..." http://rosclaw-how:8088/wiki/v1/outcomes/export \
    > data/exports/outcomes-$(date -u +%Y%m%d).jsonl

# Distill metrics
python scripts/distill_feedback.py --summary

# Apply uplift back to bridge_index
python scripts/reweight_bridge.py

# Promote staging → production / demote bad clusters
python scripts/promote.py            # dry-run by default
python scripts/promote.py --apply    # commit changes

# Reload rosclaw-how
curl -X POST -H "X-API-Key: ..." http://rosclaw-how:8088/wiki/v1/admin/reload
```

Cadence suggestion: distill + reweight + promote nightly; reload immediately
after a non-empty change set.

### E. Active learning (Phase 7 — cold-spot autodraft)

```bash
# Run periodically (cron or systemd timer)
python scripts/autodraft.py --then-ingest
# Then reload rosclaw-how (autodraft already calls ingest.py inline)
```

This polls `/wiki/v1/blind_spots`, asks DeepSeek to draft fill-in
markdown for high-frequency cold-spots, writes to `wiki/auto_drafted/`,
and runs the ingest pipeline. New clusters land at `priority: 0` so
they're trial-only until feedback matures them.

## Backup strategy

What is **state** (back this up):

| Path | What |
|---|---|
| `data/assets/bridge_index.json` | The live contract with rosclaw-how. JSON; diff-friendly. |
| `data/assets/code_patterns/` | Per-pattern markdown files; pair with bridge_index. |
| `data/source_manifest.json` | Incremental dirty-detection cache. Safe to lose; you'll just re-mine. |
| `wiki/auto_drafted/` | Phase 7 autodraft output; durable. |
| `wiki/awesome_corpus/` | Phase 8 fetched material; rebuildable from awesome URLs. |
| `data/benchmarks/*/report.json` | Verification history. Optional but useful for audit. |

What is **cache** (you can delete and re-derive):

| Path | What |
|---|---|
| `data/rosclaw_knowledge.db` | Extracted-pages SQLite. Re-derives from wiki/ via harvester. |
| `data/exports/outcomes-*.jsonl` | Snapshots from rosclaw-how `/outcomes/export`. Re-fetchable. |
| `data/stats_history/` | Phase 6 trend snapshots. Re-snapshot via `analyze_stats.py`. |

## Troubleshooting

### LLM API errors mid-mine
The harvester and Muse both retry with exponential backoff (3 attempts).
On final failure they return `None` and the affected entry is skipped —
no garbage cluster is minted. Audit conversion rate via
`data/benchmarks/*/report.json`; a sudden drop suggests upstream issue.

### Bridge / pattern file mismatch
```bash
python scripts/lint_bridge.py
```
Reports orphan pattern files, missing pattern files referenced by clusters,
duplicate `standard_name` values, and `priority=-1` clusters older than
`--stale-days`.

### Phase 4 metrics disappeared after re-mine
A full `run_phase1.py` (not `ingest.py`) **does** rebuild `bridge_index.json`
from scratch. To preserve feedback metrics, run `scripts/reweight_bridge.py`
immediately after — it merges existing `data/assets/pattern_metrics.json`
back into the fresh bridge.

### Reload takes minutes instead of seconds
That means rosclaw-how is doing a content re-hash of every cluster —
expected after a full `run_phase1.py` or a corpus expansion. Subsequent
reloads use the delta cache (~5 s for no-change runs).

## Joint verification scripts

Run these against a live rosclaw-how on `:8088`:

```bash
python scripts/verify_phase5_ingest.py     # ingest + hot-reload + CATALYST round-trip
python scripts/bench_phase6.py             # SLO baseline
python scripts/verify_phase7_active.py     # full self-improvement loop
python scripts/verify_phase8_awesome.py    # awesome-list ingest → staging routing
python scripts/replay_benchmark.py         # 60-rollout uplift A/B
```

All five PASS on the current commit (`0fb973b`).

## Versions reference

| Component | Version |
|---|---|
| rosclaw-know | 0.8.1 (Phase 8 hardening) |
| rosclaw-how (peer) | ≥ 0.1.0 with Phase 7+ endpoints |
| DeepSeek API | chat-completions v1 |
| sentence-transformers | paraphrase-multilingual-MiniLM-L12-v2 (384-dim) |
| pylibseekdb | embedded mode; 4 GB datafile reservation on disk |
