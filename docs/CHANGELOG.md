# Changelog

All notable changes by phase. Most recent first. Format inspired by
[Keep a Changelog](https://keepachangelog.com/).

## [1.5.0.dev1] — 2026-06-03 · v1.5 Sprint 2 — Benchmark Task Cards

### Added — Sprint 2 (Frontier-Eng → TaskCard catalog)

- `src/rosclaw_know/extractors/` — new package; first deliverable
  `benchmark_extractor.py`. Reads a Frontier-Eng / Arena task directory
  (`Task.md` + `frontier_eval/{initial_program,eval_command,constraints}.txt`)
  and emits a fully-typed `TaskCard`.  Pure-structural, no LLM calls;
  deterministic byte-identical output given the same inputs.
- `scripts/extract_frontier_task_cards.py` — CLI wrapper that walks a
  `benchmarks/` tree and writes `data/assets/task_cards.yaml`.  Has
  acceptance gate `--min-cards 47` (the Frontier-Eng v1 task count) and
  refuses to write when fewer cards were produced or when any card is
  missing `objective_direction` / `artifact_type` / `verifier_type`.
- `data/assets/task_cards.yaml` — generated catalog of **74 TaskCards**
  spanning 22 task families.  Coverage:
  - `objective_direction` / `artifact_type` / `verifier_type`: 74/74
  - `common_failure_modes`: 74/74
  - `hard_constraints`: 70/74 (4 tasks lack constraints.txt and a
    matching ## Constraints section in their Task.md)
  - Distribution: 70 python / 4 cpp · 41 checker_script / 24 simulator
    / 9 benchmark_harness · 37 World_Physics / 15 Planning_Decision /
    10 Control_Locomotion / 8 Systems_Compute / 4 Learning_Training ·
    61 maximize / 13 minimize.
- `tests/test_benchmark_extractor.py` (34 cases) — unit tests for the
  heuristics (camel→snake, artifact-from-extension, metric-anchored
  direction, parent-index detection, constraint extraction), end-to-end
  tests building synthetic Task.md trees, and integration tests against
  the real Frontier-Eng corpus (gated on its presence).

### Design notes

- **Metric-anchored direction.** Several Task.md files describe the
  objective in prose that contradicts the actual scoring direction
  (e.g. FlashAttention "minimize execution time" but
  `combined_score = 1e9 / geom_mean_ns` is maximize).  The extractor
  detects `metric_name` first, then consults a `METRIC_DIRECTION` table.
  Only metrics not on the table fall through to a prose-direction scan.
- **Parent-index filter.**  `benchmarks/EngDesign/Task.md` and
  `benchmarks/MolecularMechanics/Task.md` are TOC stubs pointing at
  subdirectories.  `is_parent_index` skips them by detecting either
  (a) a child subdir with its own `Task.md`, or (b) a child with
  `LLM_prompt.txt`, or (c) a stub body under 400 chars.
- **Recommendation map invariants.**  `FAMILY_TO_DOMAIN` and
  `FAMILY_RECOMMENDATIONS` are required to share the same key set; a
  test enforces it so a half-edit can't silently produce cards with
  empty `common_failure_modes`.  Every recommended pattern id is also
  required to exist as a curated entry in `data/assets/code_patterns/`.

### Verified

- `pytest -q` — **161 passed** (Sprint 1 baseline was 127; +34 new
  cases).
- `scripts/extract_frontier_task_cards.py --apply` — wrote 74-card
  catalog; round-trips back to TaskCard pydantic models cleanly.
- `scripts/validate_bridge.py` — still OK on migrated bridge tree.
- `scripts/migrate_assets_v1_to_v2.py --check` — clean (no drift).
- Acceptance gates from plan §11.3 — all satisfied:
  - 74 ≥ 47 task_cards ✓
  - 74/74 cards with `objective_direction` ✓
  - 74/74 cards with `artifact_type` ✓
  - 74/74 cards with `verifier_type` ✓

## [1.5.0.dev0] — 2026-06-03 · v1.5 Sprint 0 + Sprint 1

### Added — Sprint 0 (safety + sanity)

- `scripts/validate_bridge.py` — pydantic-backed v2 schema validator
  over `bridge_index.json`. Exits non-zero on any structural problem
  so it can gate CI / pre-deploy. Validates 349-cluster file in <1 s.

### Added — Sprint 1 (typed knowledge objects)

- `src/rosclaw_know/schemas.py` — pydantic v2 models for 10 typed
  knowledge artefacts (`FailureMode`, `FixPattern`, `ConstraintPattern`,
  `EmbodimentCard`, `TaskCard`, `VerifierCard`, `EvidenceTrace`,
  `SourceRecordV2`, `PatternCardV2`) plus the unified bridge view
  (`BridgeClusterV2`, `ClusterMetadataV2`, `BridgeIndexV2`). Strict
  validation: `domain` ∈ `FRONTIER_DOMAINS`, `priority` ∈
  `{-1, 0, 1, None}`, `objective_direction` ∈ `{maximize, minimize}`,
  `id` patterns enforced where it matters.
- `data/assets/failure_taxonomy.yaml` — seed catalog with 8 canonical
  `FailureMode` entries covering every curated pattern's failure
  surface.
- `scripts/migrate_assets_v1_to_v2.py` — non-destructive, idempotent
  v1→v2 migration. Adds a `metadata` block (lifecycle_status,
  source_quality, evidence aggregate, task_families,
  embodiment_types, …) to every cluster without modifying any v1
  field rosclaw-how reads. `--check` exits 1 on drift; `--apply`
  writes atomically (tmp + rename).
- `tests/test_schemas.py` (28 cases) + `tests/test_migrate_assets.py`
  (19 cases) — covers round-trip, strict validation, idempotency,
  lifecycle inference, source-quality inference, safety_label_index
  normalization, CLI exit codes.

### Changed — Sprint 0

- `config.DEEPSEEK_EXTRACTOR_MODEL` / `DEEPSEEK_MUSE_MODEL` defaults
  now point at `deepseek-chat` (battle-tested, returns `content`).
  Prior releases shipped `deepseek-v4-flash` / `deepseek-v4-pro`,
  which are reasoning models that put their answer in
  `reasoning_content` and return an empty `content` field — silently
  producing 0 extractions when `.env` was not overridden. Defaults
  are now safe.
- `scripts/verify_frontier_eng.py` — same default change.
- `AGENTS.md` "Do NOT trust" section updated: the model-default
  warning is now a history note rather than a live gotcha.
- `pyproject.toml` — added `pydantic>=2.6` and `pyyaml>=6.0` as
  runtime dependencies; added `pytest-asyncio>=0.23` to dev. Bumped
  version to `1.5.0.dev0`.

### Migrated

- `data/assets/bridge_index.json` ran through `migrate_assets_v1_to_v2.py`:
  349 clusters now carry v2 `metadata`. Lifecycle inferred from
  priority: **production 2 · staging 23 · demoted 3 · needs_validation
  321**. All v1 top-level fields preserved verbatim
  (`standard_name`, `domain`, `matched_keywords`,
  `cross_domain_analogies`, `associated_patterns`, `priority`,
  `uplift_mean`, `uplift_n`, `win_rate`, `last_seen`, `safety_label`,
  `source`). how reads it identically. `safety_label_index` values
  normalized from `str` to `list[str]`.

### Verified

- `pytest -q` — **127 passed**, 0 failed, 0 skipped, 1.99 s.
- `scripts/validate_bridge.py` — OK on migrated tree
  (`with_metadata: 349 / 349`).
- `scripts/lint_bridge.py` — no regressions (4 pre-existing
  anomalies: 1 duplicate name + 3 stale demotions, all present before
  Sprint 0).
- Backward-compat: how-style read on every cluster
  (`standard_name | domain | sorted(associated_patterns) | sorted(matched_keywords) | json(analogies) | int(priority)`)
  produces 349 OK / 0 broken.

## [0.8.1] — 2026-05-19 · Phase 8 hardening

### Fixed
- `0934eb0` — `muse._write_pattern_file` now always emits the
  Anti-pattern section. When the source has no documented failure mode,
  the body shows `_(no anti-pattern documented in source)_` instead of
  silently dropping the heading. Re-ran on `pattern_isef.md`; staging
  cluster lint goes from 22/23 to **23/23 perfect 4/4**.

## [0.8.0] — 2026-05-19 · Phase 8 — Awesome-list ingest (control + ICS)

### Added
- `d6d17f3` — README: cover Phase 8.
- `82ee0d4` — Scale awesome ingest to 47 entries → 16 new staging clusters.
  Probes: PID + dead time sim 0.82, MPC / DAE sim 0.68, ICS Modbus sim 0.52.
- `4491670` — `src/rosclaw_know/awesome_fetcher.py` (markdown + HTML-table
  parser, GitHub raw README fallback), `scripts/ingest_awesome.py` (with
  `--section`, `--limit`, `--then-ingest`), `scripts/verify_phase8_awesome.py`.
  14 unit tests; first batch over `A-make/awesome-control-theory` and
  `hslatman/awesome-industrial-control-system-security`.

## [0.7.0] — 2026-05-18 · Phase 7 — Active learning + staging maturation

### Added
- `ef6b96c` — Muse-minted clusters land at `priority: 0` (staging).
- `47b7450` — Adapt `promote.py` + `active_learning.py` to rosclaw-how's
  Phase 6/7 API changes (bucketed `/stats`, `is_staging` flag).
- `de77786` — `src/rosclaw_know/active_learning.py` (DeepSeek autodraft
  from `/wiki/v1/blind_spots`), `scripts/autodraft.py` (CLI, optional
  `--then-ingest`), `scripts/promote.py` (staging→production gate),
  `scripts/verify_phase7_active.py` (8-step joint verify, 6/6 PASS).

## [0.1.0 – 0.6.0] — 2026-05-17 · Phase 1–6 (squashed)

### Added — Phase 1 (offline refinery)
- `c4f9b3e` — Initial commit of the whole offline refinery: harvester,
  weaver, Muse, curated_patterns, pipeline orchestrator, run_phase1.py,
  inspect_samples.py, verify_frontier_eng.py.

### Added — Phase 2 (rosclaw-how joint integration)
*Code lives in rosclaw-how* — included here because pipeline assumptions
were finalised in this phase. See rosclaw-how `2fe1d24`.

### Added — Phase 3 (SeekDB production hot path)
*Code lives in rosclaw-how* (`2fe1d24`). rosclaw-know contribution:
publishing pipeline writes `bridge_index.json` in the shape SeekDB's
asset_loader expects (curated `safety_label_index` + `symptom_clusters`).

### Added — Phase 4 (feedback loop)
Included in `c4f9b3e`:
- `feedback_distill.py` (11 tests) + `scripts/distill_feedback.py`.
- `bridge_reweighter.py` (6 tests) + `scripts/reweight_bridge.py`.
- `scripts/replay_benchmark.py` — 60-rollout synthetic A/B; 6/6 patterns
  correctly classified, 3 soft-deprecated.

### Added — Phase 5 (incremental ingest)
Included in `c4f9b3e`:
- `source_manifest.py` (9 tests) — content-hash dirty detection.
- `incremental_pipeline.py` (5 tests) — selective Muse on new graph nodes,
  non-destructive bridge merge that preserves Phase 4 stats.
- `scripts/ingest.py` and `scripts/lint_bridge.py` (11 tests).
- `scripts/verify_phase5_ingest.py` PASS (TPU XLA cluster routable
  in < 1 s after `/admin/reload`).

### Added — Phase 6 (observability + perf)
Included in `c4f9b3e`:
- `stats_analyze.py` (14 tests) + `scripts/analyze_stats.py` — linear
  regression trend over `/stats` snapshots.
- `scripts/bench_phase6.py` — SLO baseline: build p95 ≤ 400 ms,
  feedback p95 ≤ 150 ms, reload-delta ≤ 5 s, export p95 ≤ 500 ms.

### Added — README + docs structure
- `a65a97c` — README rewrite covering Phase 1-7 closed loop with module
  table + architecture diagram + lifecycle diagram + joint verify list.

---

## Notes for deployers

- `data/assets/bridge_index.json` is the live contract with rosclaw-how. It
  must remain valid JSON; the `priority` field controls staging/production/
  demoted routing. Treat it as code-reviewable state.
- `data/source_manifest.json` is incremental-ingest cache. Safe to delete
  to force a full re-mine, but you lose the dirty-detection optimisation.
- `data/assets/code_patterns/` filename convention:
  - `<id>.md` — curated patterns (never wiped by Muse).
  - `pattern_<id>.md` — Muse-minted patterns (wiped at start of each full
    Phase 1 `compile_muse_assets`; the incremental pipeline does NOT wipe).
- `wiki/auto_drafted/` is Phase 7 autodraft output; treat as durable.
- `wiki/awesome_corpus/<list_slug>/` is Phase 8 fetched corpus; safe to
  re-run `scripts/ingest_awesome.py` (manifest deduplicates).
