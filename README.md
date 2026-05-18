# ROSClaw-Know

**Offline knowledge refinery.** Converts the 6,097 legacy ROSClaw Wiki pages
(declarative knowledge — paper abstracts, parameter tables) into **procedural
knowledge** (symptom → fix_pattern pairs + cross-domain analogies) that
runtime agents can actually act on.

Sister project: **rosclaw-how** (online injection layer that loads these
assets into SeekDB and serves agents at runtime).

## Quick start

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure
cp .env.example .env
# edit .env: set DEEPSEEK_API_KEY

# 3. Run pipeline (small batch first — 200 pages, ~2 RMB, ~10 min)
python scripts/run_phase1.py --max-pages 200

# 4. Audit a sample of extractions
python scripts/inspect_samples.py --n 30

# 5. Full run (after audit passes ≥85%)
python scripts/run_phase1.py

# 6. Closed-loop A/B verification on Frontier-Engineering
python scripts/verify_frontier_eng.py
```

## Architecture

```
rosclaw-know (offline, Python only)               rosclaw-how (online, SeekDB)
─────────────────────────────────                 ─────────────────────────────
Reads  wiki/*.md → SQLite                         Loads assets at startup
                                                  Reads SeekDB at runtime
Writes data/assets/bridge_index.json
       data/assets/code_patterns/*.md
─────────────────────────────────                 ─────────────────────────────
                              ▶ ▶ ▶
                  assets travel from know → how
```

The four-stage pipeline:

1. **Planner** — multi-perspective probe generation (STORM-inspired)
2. **Harvester** — async LLM extraction of symptom/fix_pattern (Open Deep Research style)
3. **Weaver** — NetworkX in-memory graph + optional SeekDB entity alignment (GraphRAG)
4. **Muse Compiler** — BFS radius=2 cross-domain analogies → Unified Diff patches (GBrain)

See [`docs/architecture.md`](docs/architecture.md) for the full data flow.

## What this replaces

The previous `rosclaw-wiki` project is being decommissioned:
- Its 6,000+ markdown pages → **raw input** to this pipeline
- Its online endpoints → reborn as **rosclaw-how**

This repository keeps the legacy wiki via symlink (`wiki/` →
`../rosclaw-wiki/wiki/`) for the duration of Phase 1.
