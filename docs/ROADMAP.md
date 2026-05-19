# ROSClaw-Know ROADMAP

Eight phases shipped. Phase 9 is the immediate next milestone (real-agent
A/B), with longer-horizon directions sketched after.

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
- Content-hash **delta-mode reload** — 113 s → 284 ms (398× speed-up on no-change re-load).
- rosclaw-know `stats_analyze.py` linear-regression trend reports.
- `bench_phase6.py` SLO baseline:
  build p95 ≤ 400 ms · feedback p95 ≤ 150 ms · reload-delta ≤ 5 s · export p95 ≤ 500 ms.

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
- **CartPole**: PID + adaptive gain — measure settle time
- **LunarLander**: PPO sub-collapse symptom recovery
- **Pendulum-SwingUp**: MPC with constraint
- **Quadrotor altitude hold** (synthetic, no Gym dep): wind disturbance rejection
- **PLC anomaly detection** (text-only): static-analysis style probe with `pattern_attkfinder`

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
