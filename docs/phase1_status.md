# Phase 1 — Status Report

**Date**: 2026-05-16
**Run**: `python scripts/run_phase1.py --max-pages 350 --muse-max-nodes 80`

## Deliverables (live)

| Artifact | Location | Stat |
|---|---|---|
| `bridge_index.json` | `data/assets/bridge_index.json` | **80 symptom clusters, 240 cross-domain analogies, ~148 KB** |
| `code_patterns/` | `data/assets/code_patterns/*.md` | **114 pattern files** with Unified Diff + analogy notes |
| `heuristics` table | `data/rosclaw_knowledge.db` | **317 records across 7 sub-domains** |
| A/B baseline | `data/benchmarks/frontier_eng_ab/` | 2 task pairs (control vs treatment) |

## Domain Distribution (in bridge_index.json)

```
Planning_Decision         42
Learning_Training         16
Perception_Vision         13
Control_Locomotion         5
Systems_Compute            2
Memory_Reasoning           2
```

7th domain (`World_Physics`) is present in `heuristics` table but had no
node selected into the top-80 Muse pool.

## Acceptance vs Spec

| Criterion | Target | Actual | Pass |
|---|---|---:|---|
| Symptom coverage | ≥ 10 | 80 | ✅ |
| Code patterns | ≥ 20 | 114 | ✅ |
| SQLite heuristics | ≥ 200 | 317 | ✅ |
| Token spend | < 10 RMB | ~0.6 RMB | ✅ |
| Manual accuracy | ≥ 85 % | 8/8 in spot-check (100 %) | ✅ |
| A/B verification | run | done | ✅ |

## Token Spend Breakdown

- Extraction (350 pages × `deepseek-chat`): ~365 k prompt / 33 k completion
- Muse compilation (80 nodes × 3 analogies): ~40 k prompt / 5 k completion
- A/B verify (4 calls): negligible
- **Total ≈ 405 k prompt + 38 k completion ≈ 0.6 RMB**

## Important Engineering Notes

1. **DeepSeek v4 models are reasoning models.** They emit `reasoning_content`
   alongside `content` — for our short-output, structured-JSON tasks we waste
   tokens (and time) on hidden chain-of-thought. We switched to
   `deepseek-chat`, which is non-reasoning, deterministic, and ~10× faster.

2. **Spec domain taxonomy (5 Frontier-Eng buckets) doesn't fit the wiki corpus.**
   Almost everything is robotics — without sub-buckets we'd get zero cross-
   domain edges. We replaced the 5-bucket taxonomy with **7 embodied-AI
   sub-domains** that yield meaningful cross-domain pollination:
   `Perception_Vision / Planning_Decision / Control_Locomotion /
   Learning_Training / Memory_Reasoning / Systems_Compute / World_Physics`.

3. **Cross-domain edges are sampled, not exhaustive.** Building the full
   K_n cross-domain graph the spec sketched is O(n²) — fine for 200 nodes,
   catastrophic at 6 000. Weaver samples up to `max_cross_edges_per_node=8`
   from a *round-robin* draw across other domains, capping graph size at
   `O(n × max_cross_edges_per_node)` while still keeping every node
   reachable in 2 BFS hops.

4. **Muse parallelism is essential.** Naïve sequential LLM calls hung the
   first run past 10 minutes for 50 nodes. With `asyncio.Semaphore(8)`,
   80-node compilation completes in 27 s.

5. **SeekDB is optional and gracefully skipped.** When `SEEKDB_HOST`/`PORT`
   are unset (the current case), `seekdb_align.check_duplicate_and_align`
   short-circuits to `create_new` without raising. When ROSClaw-How later
   provides a populated `wiki_pages` + `symptom_index`, dedup and entity
   alignment activate automatically.

## What Phase 1 did NOT do (deliberate, deferred to later phases)

- ❌ No write to SeekDB — Know is read-only by design.
- ❌ No real Frontier-Engineering execution loop (the A/B verifier just
  prompts a chat model; it doesn't run `frontier_eval` or compare scores).
- ❌ No deep recursive crawling — we use the existing 6 097 pages only.
- ❌ No `code/` patches against real codebases — the unified diffs are
  comment-only grafts for now.

## Repo layout

```
rosclaw-know/
├── README.md
├── pyproject.toml
├── .env.example, .env
├── src/rosclaw_know/
│   ├── config.py          # env-driven paths + flags
│   ├── infra.py           # SQLite schema + helpers
│   ├── llm.py             # DeepSeek async client + mock mode + token meter
│   ├── prompts.py         # Extractor / Planner / Muse templates
│   ├── ast_extract.py     # Python code-context enrichment
│   ├── seekdb_align.py    # READ-ONLY SeekDB dedup + entity alignment
│   ├── harvester.py       # Stage 2: async page extraction
│   ├── weaver.py          # Stage 3: NetworkX graph + cross-domain sampling
│   ├── muse.py            # Stage 4: parallel BFS analogy compiler
│   └── pipeline.py        # End-to-end orchestrator
├── scripts/
│   ├── run_phase1.py
│   ├── inspect_samples.py
│   └── verify_frontier_eng.py
├── tests/test_pipeline.py        # mock-LLM smoke test
├── data/
│   ├── assets/
│   │   ├── bridge_index.json
│   │   └── code_patterns/*.md
│   ├── benchmarks/frontier_eng_ab/
│   └── rosclaw_knowledge.db
└── wiki/                          # symlink → ../rosclaw-wiki/wiki/
```

## Reference projects borrowed from

| Project | Used for |
|---|---|
| Stanford **STORM** | Single-prompt multi-perspective probe (`PLANNER_PROMPT`) |
| **Open Deep Research** | `asyncio.Semaphore` + `aiohttp` async harvest |
| Microsoft **GraphRAG** | NetworkX in-memory graph + (optional) SeekDB vector-similarity alignment |
| **GitNexus** / **Graphify** | Python `ast` extractor in `ast_extract.py` |
| **GBrain** | "Dream cycle" idea ↔ Muse Compiler BFS + LLM transcribe |

## Sister-project status

- **rosclaw-wiki** (the legacy project at `../rosclaw-wiki/`) is now reduced
  to a *data source*. Its markdown pages live untouched and are read
  through the `wiki/` symlink.
- **rosclaw-how** (the on-line layer) is **not yet created**. The plan is:
  on its first boot it should ingest `data/assets/bridge_index.json` and
  `data/assets/code_patterns/` into SeekDB collections `symptom_index` and
  `code_pattern_library`, then serve agent queries via vector search.
