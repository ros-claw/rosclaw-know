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
| **3** | Trajectory mining (Python / CUDA / crypto / scheduling features) | 🟡 partial (framework + 3/4 extractors) |
| **4** | Pattern Compiler v2 (action-template markdown) | ✅ shipped |
| 5 | Physical Knowledge Graph v2 (multi-type + hybrid retrieval) | ✅ shipped |
| 6 | Evidence Loop v2 (placebo-adjusted uplift, hint_use_rate) | planned |
| 7 | Task Pack API + MCP `rosclaw_task_pack` | planned |
| 8 | Frontier-Eng strict 6-arm A/B (True/Placebo/Shuffled) | planned |
| 9 | Real-robot / sim ingest (rosbag / Foxglove / Isaac / MuJoCo) | planned |

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

### Sprint 3 — Trajectory mining (framework shipped; AES/CUDA/scheduling extractors deferred)

- New typed objects `Mutation`, `TrajectoryStep`, `Trajectory`,
  `CandidatePattern` in `schemas.py`.
- `extractors/code_diff_summarizer.py` — pure-Python AST + regex
  classifier producing abstracted (no-leak) mutation descriptions.
  Leak guard scrubs float literals from output.
- `extractors/trajectory_extractor.py` — framework + 3 feature
  extractors:
  - **PID** family (anti-windup / controller clamp / time-budget /
    optimizer-swap)
  - **Systems** cross-family (vectorize_loop / boundary validation /
    generic time-budget)
  - **Optimizer** cross-family (warm-start / generic random→structured)
- `scripts/extract_trajectory_patterns.py` — walks Frontier-Eng
  `baseline_archive/`, treats each `(exp, algo, model, task)` as a
  one-step trajectory, merges candidates across trajectories.
- Real data: **375 trajectories**, **8 merged candidates** (each with
  evidence_count ≥ 4) — well above plan §11.4's ≥100 trajectory gate.
- Tests: 22 new cases including end-to-end synthetic
  iteration-tree extraction + leak-free guarantee on real
  baseline_archive data.

Deferred to a follow-up sprint:

- **AES / CUDA / scheduling feature extractors** — needed to hit plan
  §11.4 acceptance "≥20 candidate patterns".  Slot in via the
  `FeatureExtractor` protocol; no framework change.
- **Failed-mutation extraction** — requires real iteration history
  (eval.json per step).  `from_iteration_dir` handles it; Sprint 9
  (real-robot/sim ingest) provides the data.

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

### Sprint 6 — Evidence Loop V2 (next)

Sprint 5 makes the graph and the retriever work, but the
`EvidenceTrace` node type currently has zero instances — the runtime
doesn't yet write traces.  Sprint 6 closes that loop by extending the
how-side `submit_outcome` to capture `code_diff_summary`,
`hint_features`, and `used_hint`, then training the bridge reweighter
on **adjusted uplift** (true − placebo) instead of raw uplift.

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
