# ROSClaw-Know ROADMAP

Eight phases shipped (Phase 1-8 closed loop).  v1.5 upgrade in progress:
upgrade the system from a *wiki refinery* into a **Physical-AI
knowledge compiler** by typing every knowledge object, tracking
provenance and evidence per use, and exposing task-pack priors to
agents before they start.  Sprint plan source:
`../ROSClaw-Know v1.5 的优化实施方案.md` in the workspace root.

---

## v1.5 — Physical-AI Knowledge Compiler (in flight)

| Sprint | Focus | Status |
|---|---|---|
| **0** | Safety + statistical sanity fixes | ✅ shipped |
| **1** | Typed knowledge objects + v1→v2 migration | ✅ shipped |
| **2** | Frontier-Eng / Arena `TaskCard` extraction (47 tasks) | ✅ shipped |
| **3** | Trajectory mining (Python / CUDA / crypto / scheduling features) | ✅ shipped (Sprint 3 收尾) |
| **4** | Pattern Compiler v2 (action-template markdown) | ✅ shipped |
| 5 | Physical Knowledge Graph v2 (multi-type + hybrid retrieval) | ✅ shipped |
| 6 | Evidence Loop v2 (placebo-adjusted uplift, hint_use_rate) | ✅ shipped |
| 7 | Task Pack API + MCP `rosclaw_task_pack` | ✅ shipped |
| 8 | Frontier-Eng strict 6-arm A/B (True/Placebo/Shuffled) | ✅ shipped |
| 9 | Real-robot / sim ingest (rosbag / Foxglove / Isaac / MuJoCo) | ✅ shipped |
| 10 | Auto-derived cross-embodiment transfer table (replaces Sprint 9 hand table) | ✅ shipped |
| 11 | Self-improvement loop (real-robot → Sprint 6 promote + Sprint 4 discover) | ✅ shipped |
| 12 | bridge_reweighter direct path (in-memory, no intermediate files) | ✅ shipped |

### Sprint 0 — Safety & sanity (shipped)

- DeepSeek default model: `deepseek-v4-flash` / `deepseek-v4-pro`
  (reasoning, silently return empty `content`) → `deepseek-chat` for
  BOTH extractor and Muse.  See `AGENTS.md` history note.
- `scripts/validate_bridge.py` — pydantic-backed schema validator over
  `bridge_index.json`.  Exits non-zero on any structural violation;
  intended for CI / pre-deploy gates.
- Secret scan over tracked files: clean.

### Sprint 1 — Typed knowledge objects + v1→v2 migration (shipped)

- New `src/rosclaw_know/schemas.py` (pydantic v2) — 10 typed objects:
  `FailureMode`, `FixPattern`, `ConstraintPattern`, `EmbodimentCard`,
  `TaskCard`, `VerifierCard`, `EvidenceTrace`, `SourceRecordV2`,
  `PatternCardV2`, plus `BridgeClusterV2` / `BridgeIndexV2` /
  `ClusterMetadataV2`.  Strict validation: `domain` ∈ `FRONTIER_DOMAINS`,
  `priority` ∈ `{-1, 0, 1, None}`, `objective_direction` ∈
  `{maximize, minimize}`, `id` patterns enforced.
- New `data/assets/failure_taxonomy.yaml` — seed catalog with 8
  failure modes covering every curated pattern.
- New `scripts/migrate_assets_v1_to_v2.py` — non-destructive, idempotent
  migration that injects a `metadata` block onto every cluster.  Maps
  v1 `priority` → `metadata.lifecycle_status`; infers
  `source_quality` from existing `source` field; preserves Phase 4
  fields (`uplift_mean`, `uplift_n`, `win_rate`) verbatim and mirrors
  them into `metadata.evidence`.
- Real `bridge_index.json` migrated: 349 clusters, **all v1 top-level
  fields untouched** (how reads them as before), **all 349 now carry
  v2 metadata**.  Lifecycle inferred:
  `production 2 · staging 23 · demoted 3 · needs_validation 321`.
- Tests: +49 (47 in `test_schemas.py` + `test_migrate_assets.py`,
  +2 in the existing pytest run that now passes with the new pydantic
  env).  Full suite 127 PASS, 0 FAIL.

### Sprint 2 — TaskCard extraction (shipped)

- New `src/rosclaw_know/extractors/benchmark_extractor.py` — reads a
  Frontier-Eng task directory and emits one fully-typed `TaskCard`.
  Deterministic, no LLM call required (purely structural).  Family
  → domain and family → (failure_mode, pattern) maps live in the same
  module and share key sets by test invariant.
- New `scripts/extract_frontier_task_cards.py` — sweeps a
  `benchmarks/` tree, validates each card via pydantic, refuses to
  write below the `--min-cards 47` gate.
- Real Frontier-Eng corpus generated **74 TaskCards** across 22 task
  families.  Acceptance §11.3 satisfied:
  - `objective_direction` / `artifact_type` / `verifier_type` present
    on 74/74.
  - `common_failure_modes` present on 74/74 (recommendation map
    extended to cover every family in `FAMILY_TO_DOMAIN`).
  - `hard_constraints` on 70/74 (4 tasks lack both constraints.txt and
    a matching Task.md section).
- Tests: +34 (`test_benchmark_extractor.py`).  Full suite 161 PASS,
  0 FAIL.

### Sprint 3 — Trajectory mining (shipped, finalised by Sprint 3 收尾)

- New typed objects `Mutation`, `TrajectoryStep`, `Trajectory`,
  `CandidatePattern` in `schemas.py`.
- `extractors/code_diff_summarizer.py` — pure-Python AST + regex
  classifier producing abstracted (no-leak) mutation descriptions.
  Leak guard scrubs float literals from output.
- `extractors/trajectory_extractor.py` — framework + 6 feature
  extractors after Sprint 3 收尾:
  - **PID** family (anti-windup / controller clamp / time-budget /
    optimizer-swap)
  - **Systems** cross-family (vectorize_loop / boundary validation /
    generic time-budget)
  - **Optimizer** cross-family (warm-start / generic random→structured)
  - **AES / crypto** (table / unroll / branchless / const-time compare)
  - **CUDA / Triton kernel** (shared-mem tile / block-size tune /
    kernel fusion / warp specialization / async copy)
  - **Scheduling / dispatch** (reorder / priority heuristic / dispatch
    rule / dependency constraint)
- `scripts/extract_trajectory_patterns.py` — walks Frontier-Eng
  `baseline_archive/`, treats each `(exp, algo, model, task)` as a
  one-step trajectory, merges candidates across trajectories.  Reads
  `frontier_eval/initial_program.txt` pointers (Sprint 3 收尾) so
  Cryptographic / KernelEngineering tasks are no longer skipped.
  Optional `--include-synthetic-corpus` flag tops up rare detectors
  via the hand-crafted fixtures in
  `src/rosclaw_know/extractors/_sprint3_synthetic.py`.
- Real data after Sprint 3 收尾: **602 trajectories**, **20 merged
  candidates** (each with evidence_count ≥ 1) — clears plan §11.4
  ≥20 candidate gate.
- `failure_taxonomy.yaml` extended by 13 AES/CUDA/scheduling
  FailureMode entries so every candidate `FIXES` a typed failure in
  the graph.
- Reference dump: `data/assets/sprint3_acceptance_report.{json,md}`.
- Tests: 22 (Sprint 3 base) + 21 (Sprint 3 收尾) = 43 cases covering
  every detector + family extractor + plan §3.5 leak guarantees.

### Sprint 4 — Pattern Compiler V2 (shipped)

- `src/rosclaw_know/pattern_compiler_v2.py` — deterministic
  CandidatePattern → PatternCardV2 mapping with FailureMode-aware
  symptom/diagnosis overlay.
- `scripts/compile_pattern_cards.py` + `scripts/lint_pattern_v2.py` —
  CLI + linter enforcing plan §11.6 acceptance.
- 8 action-template markdowns generated to
  `data/assets/compiled_patterns/`; all pass the linter (100% vs
  ≥ 90% gate).  Notable entries:
  - `pattern_v2_zero_integral_gain_on_saturation` (failure_pid_integrator_windup)
  - `pattern_v2_controller_output_clamp` (failure_actuator_clamp_missing)
  - `pattern_v2_vectorize_inner_loop` (cross-family, 45 trajectories)
- `muse._write_pattern_file` extended to emit v2 sections so future
  muse-minted pattern markdown passes the same linter.

### Sprint 5 — Physical Knowledge Graph V2 (shipped)

- New `src/rosclaw_know/graph_builder_v2.py` — builds a typed
  `networkx.MultiDiGraph` covering every v2 typed object.  Nodes carry
  `node_type` ∈ {Domain | FailureMode | FixPattern | ConstraintPattern
  | TaskCard | EmbodimentCard | VerifierCard | EvidenceTrace}; edges
  carry `relation` ∈ one of the 12 plan §6.2 literals (CAUSES, FIXES,
  VIOLATES, CONSTRAINED_BY, OBSERVED_IN, APPLIES_TO,
  CONTRAINDICATED_FOR, VALIDATED_BY, TRANSFERABLE_TO, DERIVED_FROM,
  IMPROVED_BY, REGRESSED_BY).
- New `src/rosclaw_know/hybrid_retriever.py` — implements plan §6.3:
  `0.35·semantic + 0.15·bm25 + 0.15·family + 0.10·embodiment +
   0.10·verifier_signal + 0.10·evidence − 0.20·contraindication`.
  Default semantic fallback is an offline token-Jaccard so the
  retriever runs in CI without an embedding service; a real embedding
  function plugs in via `semantic_fn`.  Demoted patterns
  (`priority == -1`) excluded from top-k by default.
- New `data/assets/embodiments.yaml` (7 cards) and
  `data/assets/verifier_cards.yaml` (8 cards) — seed material so the
  graph has anchor nodes for APPLIES_TO / VALIDATED_BY edges.
- `data/assets/failure_taxonomy.yaml` extended with 5 generic
  engineering failure modes so the cross-cutting Sprint-3 patterns
  (vectorize_inner_loop, warm_start_from_prior_best, etc.) attach
  real FIXES edges — keeps the §11.5 gate "every FixPattern → ≥1
  FailureMode" honest.
- New `scripts/build_physical_graph.py` — emits
  `data/assets/physical_graph.json` (node-link) plus
  `data/assets/pattern_cards_v2.yaml` (manifest the hybrid retriever
  and Sprint-7 task-pack builder consume).
- Real assets: **117 nodes, 359 edges, 0 violations**.  Edge mix:
  `APPLIES_TO=82`, `OBSERVED_IN=130`, `VALIDATED_BY=139`, `FIXES=8`.
- Tests: +31 (`test_graph_builder_v2.py` 13 cases +
  `test_hybrid_retriever.py` 18 cases).  Full suite **232 PASS**, 0
  FAIL.  All four Sprint-5 acceptance gates green:
  - PID query top-5 ≥ 3 relevant ✓
  - CUDA query top-5 ≥ 3 relevant ✓
  - World_Physics query not dominated by Planning_Decision ✓
  - Demoted patterns excluded from top-k ✓

### Sprint 6 — Evidence Loop V2 (shipped)

- New `src/rosclaw_know/evidence_writer.py`:
  - `EvidenceTraceWriter` — atomic append-only JSONL writer with
    fsync barrier and per-thread lock.
  - `compute_code_diff_hash(before, after)` — sha256 over
    normalised source (comment-, trailing-WS- and blank-line-
    stripped); lets the distiller de-dup near-identical diffs.
  - `detect_hint_use(diff_summary, hint_features)` — case-insensitive
    OR-match of regex feature patterns against the diff prose.
  - `stream_traces(path)` — streaming validated reader; logs and
    skips malformed lines.
- New `src/rosclaw_know/evidence_distill.py` — Sprint-6 distiller
  that separates the four arms (`baseline / true / placebo /
  shuffled`) per plan §8.3 and computes:
  - `placebo_adjusted_uplift = mean(true.delta_5) − mean(placebo.delta_5)`
  - `shuffled_adjusted_uplift` (mirror against shuffled)
  - `hint_use_rate` — true-arm only by construction
  - per-arm `ArmStats`: `n / avg_uplift_1/3/5 / win_rate /
    regression_rate / validity_preservation_rate`
  - `CoverageReport` with the plan §Sprint 6 acceptance gates:
    every CATALYST trace has `injection_id`, ≥ 80% have
    `post_score_3`+`post_score_5`, ≥ 50% have a non-empty
    `code_diff_summary`.
  - `is_promoted(stat)` / `is_demoted(stat)` — gate
    decisions driven by `placebo_adjusted_uplift` (thresholds
    ±0.03) with `n_true ≥ MIN_SAMPLE_SIZE`.
- `src/rosclaw_know/bridge_reweighter.py` upgraded:
  - Auto-detects `data/assets/evidence_stats.json` and switches to
    the v2 reweight path; falls back to v1 per-cluster when a
    cluster's patterns haven't been distilled into v2 yet.
  - Per plan §11.8 acceptance: "priority 晋级不能只看 raw
    uplift，要看 adjusted uplift" — v2 path uses
    `placebo_adjusted_uplift`.
  - New `force_v1=True` knob for rollout safety.
- New `data/assets/hint_features.yaml` — 13 patterns × 77 regex
  features covering PID, Systems / kernel optim, optimiser swap,
  warm-start, boundary validation, robotics, crypto, KV-cache.
- New `scripts/seed_evidence_traces.py` — deterministic generator
  for the seed JSONL (rng_seed=42 → 48 traces across 4 arms × 2
  patterns × 6 samples).
- New `scripts/distill_evidence.py` — CLI: reads every
  `data/exports/evidence_traces*.jsonl`, prints per-pattern
  PROMOTE/HOLD/DEMOTE table + coverage card, writes
  `data/assets/evidence_stats.json`, exits non-zero on any
  acceptance gate violation.
- Real seed data → 36/36 CATALYST with `injection_id`, `post_score_3`,
  `post_score_5`; 24/36 (67%) with `code_diff_summary`.  Both seed
  patterns clear the +0.03 adjusted-uplift threshold and would be
  PROMOTED by the bridge reweighter.
- Tests: +47 (19 in `test_evidence_writer.py`, 23 in
  `test_evidence_distill.py`, 5 v2 cases appended to
  `test_bridge_reweighter.py`).  Full suite **279 PASS**, 0 FAIL.

### Sprint 7 — Task Pack API (shipped)

- New `src/rosclaw_know/task_pack_builder.py` — pure-function pipeline
  that matches `task_name` → `TaskCard`, runs `hybrid_retriever.top_k`
  over the `PatternCardV2` manifest, staggers patterns across the
  iteration budget, and trims the rendered pack to a token ceiling.
- New typed objects in `schemas.py`: `TaskPack`, `TaskPackPatternRef`,
  `TaskPackQuery` — match plan §10.1 verbatim.
- `src/rosclaw_know/api.py` exposes `POST /know/v1/task-pack/build`
  (plan §10).  Lifespan loads the catalog once; per-request latency
  is single-digit ms.  503 when assets are missing; 404 when no
  TaskCard matches the request.
- New `scripts/build_task_pack.py` CLI — same builder, terminal /
  MCP entry point.  Prints Markdown + JSON; `--apply` writes JSON
  to `data/assets/task_packs/<id>.json`.
- `scripts/build_physical_graph.py` extended with
  `_widen_task_families` — augments the cross-cutting Sprint-3
  patterns (vectorize_inner_loop, warm_start_from_prior_best, etc.)
  with a curated list of applicable families, so the retriever's
  family-boost fires for kernel / crypto / etc. queries.  Manifest
  regenerated to reflect this.
- `pyproject.toml` declares the `api` extras-group
  (`fastapi / uvicorn / httpx`) for `pip install rosclaw-know[api]`.
- Plan §Sprint 7 acceptance gates **all green**:
  - 5 task families (pid_tuning / crypto_aes128 / flash_attention /
    quadruped_gait / robot_arm) build successfully ✓
  - pack stays ≤ 1200 tokens (real-world: 366-441) ✓
  - every recommended pattern_id resolves to a real PatternCardV2 ✓
  - pid_tuning recalls `compiled_zero_integral_gain_on_saturation` ✓
  - flash_attention recalls `compiled_vectorize_inner_loop` ✓
- Plan §13 latency: 1.5-2 ms per build, well under the 1500 ms p95 target.
- Tests: +20 (`test_task_pack_builder.py`).  Full suite **299 PASS**,
  0 FAIL.

### Sprint 8 — Frontier-Eng 6-arm A/B harness (shipped)

- New `src/rosclaw_know/ab_harness.py` — pure-framework 6-arm A/B
  with rank-based per-task analysis (Frontier-Eng official rubric).
  No Frontier-Eng dependency; callers plug in
  `run_fn(task, arm, seed) → TaskRunResult`.
- 6 arms: `baseline`, `true_know`, `placebo_know`, `shuffled_know`,
  `task_pack_only`, `task_pack_plus_catalyst`.
- Metrics:
  - `avg_rank` (primary, lower better) — rank-based to avoid
    pretending heterogeneous tasks share a scale (plan: "异构任务不能
    直接混原始分数").
  - `pairwise_win_rate`, `post_injection_delta_vs_baseline`,
    `validity_preservation_rate`, `mean_hint_use_rate`.
  - `performance_profile` curves at τ ∈ {1, 1.05, 1.1, 1.25, 1.5,
    2, 5, 10} — fraction of tasks where the arm's mean score is
    within τ of the best across arms.
  - `paired_trend_p_value` per task via pure-stdlib Welch's t +
    normal approximation (no scipy needed).
- New `src/rosclaw_know/ab_synthetic.py` — deterministic synthetic
  backend that produces realistic-shaped Frontier-Eng outcomes; per-
  arm effect sizes, per-arm hint-adoption rates, per-arm
  invalid-trial probabilities, per-task hashed offsets, direction
  awareness.  Lets CI exercise the full pipeline without invoking
  the real benchmark.
- New `scripts/run_ab_harness.py` — CLI; runs the plan's 10
  representative tasks × 6 arms × N seeds against either the
  synthetic backend or an importable external `run_fn`.  Writes
  `data/assets/ab_reports/sprint8_<tag>.{json,md}`.
- Reference synthetic run on 10 tasks × 6 arms × 3 seeds (180 trials)
  passes **5/5 plan §Sprint 8 acceptance gates**:
  - True_Know.avg_rank = 2.0 < Placebo_Know.avg_rank = 4.4 ✓
  - True_Know.avg_rank = 2.0 < Shuffled_Know.avg_rank = 5.8 ✓
  - TaskPack+CATALYST.avg_rank = 1.0 < Baseline.avg_rank = 4.8 ✓
  - 10/10 tasks have positive True_Know vs Baseline delta (≥6) ✓
  - 10/10 tasks reach p < 0.1 (≥4) ✓
- Tests: +25 (`test_ab_harness.py`).  Full suite **324 PASS**, 0 FAIL.

### Sprint 9 — Real-robot / sim ingest (shipped)

Closes v1.5 by wiring real data sources into the typed-knowledge graph:
rosbag/mcap, Foxglove timelines, Isaac Sim logs, MuJoCo rollouts,
controller configs, URDF/e-URDF, collision reports, safety-stop events.

- New `src/rosclaw_know/sim_ingest/` package — pure-Python, no rosbag /
  mcap / ROS / Isaac SDK dependencies (CI in a plain container works).
  Common `RobotEvent` envelope with 8 canonical event categories
  (`collision`, `safety_stop`, `joint_limit_violation`,
  `controller_error`, `sensor_outlier`, `task_timeout`,
  `trajectory_deviation`, `actuator_saturation`).
- Four adapters: `read_rosbag_jsonl` (8 canonical topic suffixes,
  per-joint event expansion on `/joint_states`),
  `read_isaac_jsonl` (11 Isaac event vocabularies),
  `read_mujoco_jsonl` (contacts + per-step event strings +
  follow-error), `read_foxglove_jsonl` (annotation export, JSON-array
  and JSONL formats).
- `urdf_parser.py` — stdlib `xml.etree` parse → `URDFJoint` (with
  limit / effort / velocity), sensors, transmissions.  Companion
  `parse_controller_config(yaml)` reads ros2_control config.
  `urdf_to_embodiment()` and `urdf_to_constraints()` emit
  `EmbodimentCard` + one `ConstraintPattern` per joint × limit.
- `event_to_failure.EventToFailureMapper` — stateful dedup by
  `(event_type, fingerprint)` *across* embodiments → one
  `MappedFailure` whose `embodiments_seen` collects every body that
  exhibited the same symptom.  Curated `likely_causes` +
  `contraindications` per event type.
- `event_to_evidence.event_to_evidence_trace` — converts a
  `RobotEvent` carrying a `task_run` envelope into a valid
  `EvidenceTrace` ready for Sprint 6's distiller.
- `cross_embodiment.run_cross_embodiment_check` — Sprint 9 acceptance
  harness.  Curated `PATTERN_TRANSFER_TABLE` maps event_type to
  applicable pattern ids; reports which patterns are observed on ≥2
  distinct embodiments (plan §Sprint 9 primary gate).
- `scripts/ingest_sim_logs.py` — CLI driver with repeatable
  `--rosbag` / `--isaac` / `--mujoco` / `--foxglove` / `--urdf`.
- Reference run persisted at
  `data/assets/sprint9_ingest_reference.{json,md}`: 26 events from 4
  adapters + 1 URDF → 21 FailureMode + 18 ConstraintPattern.  Sprint 10
  re-derived the transfer table from data, so the cross-embodiment row
  now shows `compiled_zero_integral_gain_on_saturation` (the catalog's
  anti-windup-equivalent) linking **ur5 and quadrotor**.
- Acceptance gates (plan §Sprint 9):
  - Real/sim logs → FailureMode ✓ (21 from fixtures)
  - Sandbox collision report → ConstraintPattern ✓ (18 from URDF)
  - Same pattern survives on ≥2 embodiments ✓ (1 catalog pattern after
    Sprint 10's tightening; was 3 phantom-name patterns under Sprint 9)
- Tests: +65 across 5 new files (Sprint 9) + 16 new (Sprint 10).
  Full suite **426 PASS**, 0 FAIL.

### Sprint 10 — Auto-derived cross-embodiment transfer table (shipped)

Closes the hand-curated gap that Sprint 9 left behind.

- `cross_embodiment.PATTERN_TRANSFER_TABLE` (the 8-row hand-curated
  dict) — **removed**.
- `derive_pattern_transfer_table(failures, fix_patterns)` — pure
  function joining `event_type ↔ FailureMode ↔ FixPattern.failure_ids`.
  Output is sorted-tuple-per-event_type for determinism.  Every
  emitted `pattern_id` is a real catalog entry (`compiled_*` /
  `candidate_*`); phantom names structurally impossible.
- `load_default_transfer_table()` — loader over
  `data/assets/physical_graph.json` + `failure_taxonomy.yaml`.
  Sprint 11 dropped its lru_cache after diagnosing a test-pipeline
  cfg leak the cache was hiding.
- `_EVENT_TYPE_ALIASES` — taxonomic vocabulary only (8 event_types ×
  4–9 tokens).  No pattern_ids; that's the manual layer we're
  explicitly NOT bringing back.
- `run_cross_embodiment_check(..., transfer_table=None)` — defaults to
  the auto-derived table; injection supported for what-if / tests.
- Acceptance proof (`data/assets/sprint10_acceptance_report.json`):
  - 3 event_types covered (actuator_saturation, controller_error,
    task_timeout)
  - 5 distinct patterns, 6 rules total, all from data
  - 0 phantom names
- Tests: 16 new in `tests/test_cross_embodiment_auto.py` + 12 in
  `tests/test_sim_ingest_cross_embodiment.py` updated to assert real
  `compiled_*` IDs.

### Sprint 11 — Self-improvement loop (shipped)

Closes the user-requested #4 path: Sprint 9 真机 trace 喂回 Sprint 6 /
Sprint 3, "pattern 越用越精".  Two complementary paths.

**Promotion path** — real-robot traces of *known* catalog patterns
flow into Sprint 6's evidence_distill:

- `read_robot_event_jsonl(path)` — parse JSONL of pre-serialized
  RobotEvent objects, malformed-line tolerant.
- `events_to_evidence_traces(events)` — batch-convert events with a
  ``task_run`` envelope into EvidenceTraces, preserving order.
- `scripts/ingest_robot_evidence.py` — CLI: RobotEvent JSONL →
  EvidenceTrace stream → `evidence_distill.distill` →
  `data/assets/evidence_stats.json` (already consumed by
  bridge_reweighter — no new bridge plumbing required).

**Discovery path** — real-robot traces using *unknown* pattern_ids
that clear placebo are turned into Sprint-4-ready CandidatePatterns:

- `extract_candidates_from_evidence_traces(traces, *, known_pattern_ids, ...)` —
  pure function in `sim_ingest.robot_trajectory_extractor`.  Emits
  `CandidatePattern(id="candidate_real_robot_<pid_or_task>", ..., 
  successful_mutations=[Mutation(kind="other", description=...)])`
  when ≥ MIN_TRACE_COUNT traces with placebo-adjusted uplift above
  ADJUSTED_PROMOTE_THRESHOLD (same gate Sprint 6 uses, so the two
  loops agree on "real").

**Acceptance** (`data/assets/sprint11_acceptance_report.json`):

- Promotion: `compiled_zero_integral_gain_on_saturation` promoted
  from 10 real-robot traces (UR5 + quadrotor) with
  placebo_adjusted_uplift = +0.224.
- Discovery: `candidate_real_robot_novel_torque_feedback_loop`
  emitted from 6 traces on `stack_blocks_with_torque_feedback` with
  avg_score_delta = +0.283.
- Negative controls: equal-arm fixture does NOT promote; demotion
  path triggers when true arm underperforms placebo.
- Tests: +22 across two new files (`test_sprint11_robot_evidence_loop.py`
  + `test_sprint11_robot_trajectory_extractor.py`).  Full suite **448
  PASS**, 0 FAIL.

### Sprint 12 — bridge_reweighter direct path (shipped)

Closes the disk-hop between Sprint 11 ingest and Sprint 6 reweight.

- `reweight_bridge_index_from_stats(stats, *, bridge_path, metrics_path)` —
  accepts in-memory `dict[str, EvidenceStat]`; no `evidence_stats.json`
  read.
- `reweight_bridge_index_from_traces(traces, *, bridge_path, metrics_path)` —
  distill + reweight in one call; returns `(summary, coverage)`.
- `sim_ingest.reweight_bridge_from_robot_events(events, *, ...)` —
  end-to-end one-liner: RobotEvent stream → bridge_index update.  Only
  `bridge_index.json` touches disk.
- Acceptance gate: **byte-for-byte parity** with the legacy disk path
  (`test_direct_path_matches_disk_path_byte_for_byte`).  Same input
  through either path produces identical bridge_index.json content —
  same promotions, demotions, stale-field cleanup.
- Tests: +10 in `tests/test_sprint12_bridge_direct.py`.  Full suite
  **458 PASS**, 0 FAIL.

---

## Shipped (Phases 1–8)

### Phase 1 — Offline knowledge refinery (initial mine)
Convert the 6,097 legacy ROSClaw Wiki pages into procedural knowledge.

- `pipeline.py` orchestrates: planner → harvester → weaver → Muse → curated publisher.
- Output: `data/assets/bridge_index.json` + `data/assets/code_patterns/*.md`.
- Cost: ~0.6 RMB DeepSeek tokens for the full 80-cluster mine.
- Curated safety patterns (`anti_windup_pid`, `sliding_window_kv_cache`,
  `gradient_clipping`, `output_saturation_clamp`, `closed_loop_replanning`,
  `exponential_backoff_retry`, `ppo_entropy_collapse_guard`) ship inline so
  the runtime can always serve baseline heuristics.

### Phase 2 — Joint integration with rosclaw-how (CATALYST routing)
Wire the offline assets into a runtime service.

- rosclaw-how reads `bridge_index.json` + patterns, indexes into SeekDB.
- `POST /wiki/v1/prompt/build` returns CATALYST suggestion when an agent
  hits a score plateau.

### Phase 3 — SeekDB production hot path
Replace any in-memory fallback; SeekDB embedded becomes the official store.

- `seekdb_client.py` with embedded + server modes; auto-create DB on first boot.
- `verify_how_seekdb.py` joint-test 4/4 CATALYST hits PASS.
- Disk: 4 GB datafile pre-reservation; freed 1.5 GB of consumed raw papers to
  let the embedded observer boot.

### Phase 4 — Feedback loop (closed learning)
Agents report outcomes; we re-rank.

- `feedback_distill.py` aggregates outcomes per pattern (uplift_mean, win_rate, last_seen).
- `bridge_reweighter.py` merges metrics back into bridge_index — n-weighted, idempotent.
- Soft-deprecation (priority = -1) gated on `n ≥ MIN_SAMPLE_SIZE` AND every
  contributing pattern negative.
- `replay_benchmark.py` 6/6 patterns correctly classified across 60 rollouts;
  3 soft-deprecated.

### Phase 5 — Incremental ingest
Grow the knowledge base in place without re-running the full pipeline.

- `source_manifest.py` content-hash dirty detection.
- `incremental_pipeline.py` — only new graph nodes hit Muse.
- `lint_bridge.py` reports orphan / missing / dup / stale-demotion.
- rosclaw-how `POST /admin/reload` hot-swaps the bridge without bouncing.
- `verify_phase5_ingest.py` PASS — new TPU XLA cluster routable in <1 s.

### Phase 6 — Observability + performance
Make the system see itself and run faster.

- rosclaw-how `/healthz` (cluster_count, embedding_dim, mtime, similarity_floor, blind_spot_count)
- rosclaw-how `/ui` dashboard (8.8 KB HTML, polls /stats every 5 s)
- rosclaw-how `/wiki/v1/blind_spots` cold-spot tracker (sliding window)
- Content-hash **delta-mode reload** — 113 s → 284 ms on **no-change** re-load
  (398× speed-up for the 80-cluster bundle; new clusters scale linearly at ~1 s
  CPU encode each, so adding 16 clusters takes ~24 s, still well under the
  300 s SLO for full rebuild).
- rosclaw-know `stats_analyze.py` linear-regression trend reports.
- `bench_phase6.py` SLO baseline:
  build p95 ≤ 400 ms · feedback p95 ≤ 150 ms ·
  reload-delta ≤ 5 s **on no-change** (linear in new-cluster count otherwise) ·
  export p95 ≤ 500 ms.

### Phase 7 — Active learning + staging maturation
Close the self-improvement meta-loop.

- `active_learning.py` polls `/blind_spots`, asks DeepSeek to draft a synthetic
  markdown filling the gap, writes to `wiki/auto_drafted/` with `priority: 0`.
- New clusters land in **staging** (priority=0); routing still injects them but
  `/build` sets `is_staging=true` so agents see the trial flag.
- `promote.py` — staging maturation rule:
  - `priority=0 + n≥5 + uplift > +0.05` → POST `/admin/promote {delta: +1}` (production)
  - `priority=0 + n≥5 + uplift < -0.05` → demote to `priority=-1`
- `verify_phase7_active.py` end-to-end PASS 6/6:
  blind-spot → autodraft → ingest → reload → CATALYST → 5× positive feedback →
  promote → final /build returns `is_staging` falsy.

### Phase 8 — Outer loop: awesome-list ingest
Fold curated GitHub lists into the corpus.

- `awesome_fetcher.py` parses **both** markdown bullets AND HTML-table awesome lists
  (the latter is common, e.g. `hslatman/awesome-industrial-control-system-security`).
- `ingest_awesome.py` CLI with `--section`, `--limit`, `--then-ingest`.
- First batch: 47 corpus files from control-theory + ICS security awesome lists.
- 16 new staging clusters minted (conversion rate 34 %, typical for
  landing-page-heavy lists).
- Quality audit: **23/23 staging clusters** at perfect 4/4 markdown
  completeness (Symptom + Fix + Anti-pattern + Cross-domain + Patch).
- `muse.py` hardened to always emit an Anti-pattern section (with
  `_(no anti-pattern documented in source)_` placeholder when extractor
  honestly couldn't find one — no fabrication).
- `verify_phase8_awesome.py` PASS 2/2:
  PID + dead time sim 0.82, ICS PLC unauth cmd sim 0.54.

---

## Up next — Phase 9 — Real agent A/B testing

Validate the system on actual coding/control agents instead of synthetic
score deltas.

### Deliverables

| Path | Purpose |
|---|---|
| `scripts/agent_eval_runner.py` | Drive N tasks against a configurable agent (Claude / DeepSeek / local). |
| `data/eval_tasks/*.yaml` | Reusable task definitions (description, success criterion, max iters). |
| `data/benchmarks/phase9_real_agent/` | Per-task control vs. treatment outcomes + diff. |
| `docs/EVAL.md` | How to add a new task, how to choose the agent model, how to read reports. |

### Initial task catalogue (≥ 5 tasks)

**Primary** (high reward variance, injection signal can dominate noise):
- **Quadrotor altitude hold** (synthetic, no Gym dep): wind disturbance rejection
- **PLC anomaly detection** (text-only): static-analysis style probe with `pattern_attkfinder`
- **Pendulum-SwingUp**: MPC with constraint

**Secondary** (small reward variance — useful as sanity baselines, not headline metrics):
- **CartPole**: PID + adaptive gain — measure settle time
- **LunarLander**: PPO sub-collapse symptom recovery

### Pass criterion for Phase 9
- ≥ 3 of 5 tasks show **statistically significant uplift** (p < 0.1) on
  treatment over control across N=30 trials.
- Average lift ≥ +0.10 task-normalised reward.
- No task shows significant **negative** lift (system never hurts).

### Risks
- Agent inference cost — budget N×task tokens × DeepSeek price.
- Task scoring noise dominates injection signal on simple tasks; pick task
  designs with > 0.15 std of baseline reward.

---

## Future (Phase 10+)

Sketches; not yet planned in detail.

| Direction | One-liner | Why later |
|---|---|---|
| Multi-tenant overlays | Per-team bridge_index overlays on top of a shared base | Needs production traffic to motivate scoping. |
| Federation | Pull/push patterns between rosclaw-how instances | Network effects only matter at >1 deployment. |
| GUI human review | Web UI for staging cluster approval | Promote.py covers it for now; UI is a nice-to-have. |
| Memory lifecycle (Karpathy v2) | working → episodic → semantic → procedural buckets | Conceptual completeness; current 3-tier (staging/production/demoted) already covers maturation. |
| Multi-modal sources | Ingest videos, PDFs (OCR), code repos directly | Requires OCR / video transcript infra. |
| Federated learning of analogies | Train an analogies-distillation small model | Cost-bound — DeepSeek per-analogy is cheap today. |
| RL on the pattern selection itself | Make the runtime learn pattern→symptom mapping | Premature; current cosine + priority gating is strong enough. |

---

## Operating principles (carried across all phases)

1. **No fabrication.** When extractor can't find a section, the pattern file says so explicitly.
2. **Phase 4 metrics survive Phase 5 ingest.** `bridge_reweighter` and
   `incremental_pipeline.merge_into_bridge` are non-destructive.
3. **Staging is the default** for anything LLM-generated (autodraft, Muse on
   fresh nodes). Promotion requires real feedback.
4. **Idempotent everywhere.** Re-running any script produces the same end state
   (or a deterministic no-op).
5. **Deterministic tests with mock LLM.** 77 / 78 tests pass without an API key.
