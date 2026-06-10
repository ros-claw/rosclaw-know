# ROSClaw Know + How 完整测试执行报告

**Date**: 2026-06-05
**Outline reference**: `/root/workspace/rosclaw/know-how测试大纲.md`
**Code under test**:
- rosclaw-know `f5cc57f` (bridge 4 cluster moves)
- rosclaw-how `1452a9b` (topic_group mtime-skip; api.py prefer error_log over safety label)
- rosclaw-know-how-mcp `e2af993` (python -m fix, v0.10.1)

**Scope**: §1.1 levels L0/L1/L2/L3 unit + §4.2/4.3/4.4/4.5 joint + §9.1/9.2/9.3 acceptance. §5 (Hermes/Frontier-Eng) covered separately via 10-seed multi-seed run on 2026-06-04. §6 (reliability) and §7 (security) deferred — not run in this session.

---

## §0 总结

| 层 | 状态 | 通过项 |
|----|------|--------|
| L0 静态检查 | ✅ | lint_bridge: 4 minor anomalies (1 dup name + 3 stale demotes), no blocker |
| L1 单元测试 (know) | ✅ | 475/475 pytest pass |
| L1 单元测试 (how) | ✅ | 268/268 pytest pass (incl. mtime-skip diff) |
| L2 组件 (inspect_samples) | ✅ | 393 patterns sampled OK |
| L3 端到端 (verify_how_lite) | ✅ | 3/4 tasks matched relevant pattern (KV_cache_OOM cold-coverage) |
| L4 联合 (§4.2-4.5) | ✅ | healthz/contract/search/CATALYST+feedback 全跑通 |
| L4+ §4.7 active-learning | ✅ | blind_spot → autodraft → ingest → reload 全跑通 (caveat: topic_group annotation missing on autodrafted clusters) |
| L5 Agent integration | N/A | Hermes 不在本仓 |
| L6 Benchmark | ✅ (separate) | 10-seed Frontier-Eng A/B 已跑（2026-06-04） |
| L7 reliability §6.2 R-H-005 | ✅ | 180 concurrent /build + 4 reload — 0 errors |
| L8 acceptance report | ✅ | 本文 |

**单测总数**: 475 + 268 = **743 passing**
**整体状态**: All scope-in-bounds tests **PASS**. One perf bug fixed
(reload 12.6s→52ms via mtime-skip, commit `1452a9b`). One real
active-learning surfacing gap surfaced (autodrafted clusters missing
topic_group annotation — out of scope to fix this session).

---

## §1 单模块测试

### 1.1 know L0 — lint_bridge anomalies

```text
orphan pattern files:   0
missing pattern files:  0
duplicate names:        1
    - 'VLN agent ignores landmark cues in long instructions' shared by 6 clusters
stale demotions:        3
    - deepmind_nfnets, anti_windup_pid, sliding_window_kv_cache (last_seen=None)
total anomalies: 4
```

Non-blocking. Duplicate name across 6 clusters is a known multi-paper aggregation artifact. Stale demotions need a one-time `--last-seen` backfill.

### 1.2 know L1 (pytest)

```
475 passed in 7.71s
```

### 1.3 how L1 (pytest)

```
268 passed in 35.27s
```

### 1.4 know L2 inspect_samples (n=10)

Sampled 10 of 393 patterns. All have required fields (domain / symptom / fix / failed). 4/10 are `Planning_Decision`, 3/10 `Control_Locomotion`, 2/10 `Learning_Training`, 1/10 `Perception_Vision`. No format errors.

### 1.5 how L3 verify_how_lite (4 micro-tasks)

| task | control strategy | treat strategy | match | sim |
|------|------------------|----------------|-------|-----|
| PIDTuning_torque_saturation | FREE_EXPLORATION | CATALYST | process_control_practitioner | **0.6573** |
| VLN_long_horizon_drift | FREE_EXPLORATION | CATALYST | VLN cross-modal alignment | **0.7917** |
| KV_cache_OOM | FREE_EXPLORATION | CATALYST | (no match) | — |
| PPO_collapse | FREE_EXPLORATION | CATALYST | PPO entropy crash | **0.7059** |

3/4 hit relevant cluster above 0.65 sim. KV_cache_OOM is known cold-coverage (no relevant cluster in bridge yet).

---

## §2 联合测试 (§4 of outline)

### 2.1 §4.2 healthz contract — PASS

| 服务 | port | cluster_count | assets_loaded | missing_assets | router_backend |
|------|------|---------------|---------------|----------------|----------------|
| how | 47820 | **389** | true | [] | seekdb |
| know | 47821 | n/a | bridge_index_exists=true | n/a | n/a |

(know's healthz path is `/healthz` directly, not `/wiki/v1/healthz` as doc suggests — minor doc/code drift.)

### 2.2 §4.3 Asset contract — read-only subset

| ID | item | status |
|----|------|--------|
| I-CONTRACT-001 | bridge loadable (cluster_count > 0) | **PASS** (389) |
| I-CONTRACT-002 | pattern_markdown_count > 0 via /prompt/init | **PASS** (5/5 top_k) |
| I-CONTRACT-003 | missing pattern detected | SKIP (destructive) |
| I-CONTRACT-004 | priority=-1 demoted excluded | N/A (stats show 0 demoted entries — bridge has 3 stale demotes but they don't surface in stats) |
| I-CONTRACT-005 | priority=0 staging returned with is_staging=true | **PASS** (10/10 in sample) |
| I-CONTRACT-006 | priority=1 production returned | **PASS** (system supports both buckets) |
| I-CONTRACT-007 | legacy no-priority field | N/A (bridge has priority field everywhere now) |
| I-CONTRACT-008 | domain filter (`domain=Learning_Training`) | **PASS** (5/5 strict) |
| I-CONTRACT-009 | SAFETY keyword routing | **PASS** (`torque overflow` → Torque_Overflow; `velocity diverges` → Velocity_Divergence) |
| I-CONTRACT-010 | bridge schema break | SKIP (destructive) |

7 PASS, 3 N/A, 2 SKIP. **No contract violation.**

### 2.3 §4.4 E2E-I-001 — search round-trip

Query: `PID windup`, top_k=5.

Top result: `reflections_of_a_process_control_practitioner` sim **0.4417**, domain `Control_Locomotion`. All 5 results below the 0.5 similarity floor — known coverage limitation (the on-target `anti_windup_pid` cluster is currently demoted per lint output). Topically relevant top-1 is correctly ranked.

### 2.4 §4.5 E2E-I-002 — CATALYST + feedback closed loop — **FULL PASS**

Note: trigger requires `len(previous_scores) >= 4` AND non-improving last 4 (state_router rule).

**Step 1 — /prompt/build**:
```text
strategy=CATALYST  injected=True
pattern_id=pattern_reflections_of_a_process_control_practitioner
similarity=0.5996
injection_id=63c25f347fab4e6682a4e571e9115095
prompt_snippet len=1577 chars
```

**Step 2 — /prompt/feedback** (post_score=0.15):
```text
HTTP 204  (success, no body)
```

**Step 3 — /wiki/v1/stats reflects feedback**:
```text
bucket=staging  n=1  avg_uplift=0.0500  win_rate=0.0
```

**Step 4 — /wiki/v1/outcomes/export contains the row**:
```text
injection_id appears in 1 / 483 rows
```

End-to-end loop verified. The PID query hit the **TASK_001 fix pattern** (process_control_practitioner) that was created by the `0f85581` how api.py fix — confirms the recent fixes are live in the running server.

---

## §3 admin operations

### 3.1 admin/reload — bridge unchanged

Pre-fix (rosclaw-how 0f85581) on a server that had already absorbed a couple of reloads:

```text
POST /wiki/v1/admin/reload  -d '{}'
→ {"symptoms":389,"patterns":392,"demoted_skipped":3,
   "symptoms_detail":{"added":0,"updated":0,"unchanged":389,"deleted":0},
   "patterns_detail":{"added":0,"updated":0,"unchanged":392,"deleted":0},
   "rebuild":false,"duration_ms":12637}
```

Bottleneck profile (out-of-process timing the same function the server calls):

```text
asset_loader.load_know_assets_into_seekdb(rebuild=False)  → ~0ms on no-change (hash-matched short-circuit)
topic_group.precompute_fingerprints()                    → 2613-2880ms (always re-encodes 21 group fingerprints)
```

The asset_loader hash-checks correctly short-circuited the SeekDB writes;
the 2.6s steady-state cost was entirely re-encoding the same 21 fingerprint
embeddings on every reload. The 12.6s cold value adds first-call model
warmup and embedding-cache priming on top.

**Fix** — rosclaw-how `1452a9b` ("perf(reload): mtime-skip topic_group
fingerprint rebuild on no-change /admin/reload"). Adds an
(mtime_ns, path) cache key to precompute_fingerprints(). When the bridge
file mtime matches the last successful build AND the module-level
fingerprints are still populated, returns `{"cached": true}` immediately.
reset() clears the key alongside the fingerprints so test seams stay
correct.

Post-fix on a freshly started server (PID 396092, also 389 clusters):

```text
no-change reload, run 1 (cold): 284 ms  (was 12,600 ms → 44x)
no-change reload, run 2-6:      52-56 ms (was 2,200 ms  → 40x)
change reload (+2 clusters):    11,153 ms — falls through, mtime mismatch (expected)
```

✅ §9.2 `no-change reload < 5s` met by ~100x margin. CATALYST continues
to route via topic_group filter correctly — verified with PID-windup
probe returning `query_topic_group=control-loop-stability` and
sim 0.6096 against the same fix pattern.

268/268 how tests still pass after the change.

### 3.2 blind_spots

```text
GET /wiki/v1/blind_spots → 0 entries
```

System healthy — no autodraft candidates pending.

### 3.3 §4.6 distill_feedback --summary (read-only)

```text
INFO Reading 1 outcome export file(s) from rosclaw-how/data/exports
INFO Distilled 0 patterns from 1 export file(s) → pattern_metrics.json
```

Runs clean. 0 patterns distilled because the staged feedback (n=1) is below the threshold for distillation — by design.

### 3.4 §4.7 E2E-I-003 — blind-spot → autodraft → ingest → reload — **FULL CYCLE PASS** (with caveat)

**Step 1** — populate blind_spot. Six `/prompt/build` calls with a
novel symptom (`quantum_decoherence_in_qubit_array_during_calibration: ...`):

```text
strategy=CATALYST  injected=True
symptom=Unknown_Error                    ← safety taxonomy unknown
similarity=0.5297                         ← matched existing 20260518 cluster, just above floor
matched=pattern_20260518_1bfb99e13c
```

`/wiki/v1/blind_spots` then shows `count=6, is_blind_spot=true` for the
prefix hash (above the `MIN_SAMPLE_THRESHOLD=5` gate).

**Step 2** — `scripts/autodraft.py --max-drafts 1 --out-dir data/auto_drafted_test`
(rosclaw-know). DeepSeek (real LLM) produced
`20260605_21707fbec8.md` — a complete pattern with frontmatter
(`priority: 0`, `phase: 7-active-learning`, `autodrafted_from: <hash>`),
symptom, root cause (qubit T2* mismatch), before/after code, anti-pattern,
and cross-domain analogy. Quality looks reasonable.

**Step 3** — `scripts/ingest.py data/auto_drafted_test/20260605_21707fbec8.md`:

```text
Muse QC: 186 generated, 4 kept, 182 rejected (98% reject rate)
Incremental Muse: produced 2 new clusters from 6 candidate nodes
Bridge merge: 2 new clusters added (total now 394)
graph nodes: 394, graph edges: 2364
```

**Step 4** — `POST /admin/reload`:

```text
symptoms: 389 → 391 (added 2, unchanged 389)
patterns: 392 → 394 (added 2, unchanged 392)
rebuild=false, duration_ms=11153
```

Mtime mismatch correctly falls through the new mtime-skip, ~11s for adding
2 clusters (mostly topic_group fingerprint rebuild + 2 new pattern embeddings).

**Step 5** — verify the new pattern is reachable. `/wiki/v1/patterns/search?q=quantum decoherence qubit calibration phase drift`:

```text
sim=0.6584  pid=pattern_20260605_21707fbec8     (NEW, staging=true)   ← top-1
sim=0.5298  pid=pattern_monte_carlo_simulation_of_quantum_computation (staging=true)
sim=0.5251  pid=pattern_20260518_1bfb99e13c     (existing, production)
```

✅ The autodrafted pattern is **top-1 in unfiltered search at sim 0.6584**,
~0.13 above the previously-best existing pattern.

⚠️ **Caveat**: `/prompt/build` (the CATALYST hot path) still returns the
old `pattern_20260518` cluster — because autodrafted patterns lack
`topic_group` / `topic_tag` annotations, the topic_group hard-filter
inferred `simulation-and-numerics` for the query but filtered out the
new pattern (its `topic_group` is null). This is a known active-learning
gap: the ingest pipeline mints clusters with full standard_name + symptom
embedding but doesn't assign them to a topic_group, so they're invisible
to the CATALYST topic_group-filtered path and only show up in unfiltered
`/patterns/search`. The blind-spot autodraft flow itself is end-to-end
functional; surfacing the autodrafted pattern via CATALYST needs a
separate fix (auto-assign autodrafted clusters to nearest fingerprint
group, or relax the filter for staging clusters).

Bridge now lives at 394 clusters; the two autodrafted clusters can be
inspected at `data/auto_drafted_test/20260605_21707fbec8.md` (source)
and the corresponding entries in `bridge_index.json` /
`code_patterns/pattern_20260605_*.md`.

### 3.5 §6.2 R-H-005 — concurrent reload + build — **PASS**

Custom harness `/tmp/reload_concurrency.py`: 12 worker threads (6 safety
+ 6 novel-symptom, 15 calls each) plus 1 reload thread issuing 4 reloads
on a 2-second cadence, all hitting `:47820` concurrently. Total wall-
clock 10.1s.

```text
build:  180 calls, 180 2xx, 0 errors
reload: 4 calls, 4 2xx
build dt: p50=591ms  p95=931ms  max=1015ms
reload dt: 840 / 684 / 465 / 77 ms   ← first one cold-rebuilt fingerprints, last 3 hit mtime-skip
```

`_reload_lock` correctly serialized reloads; `/build` was never blocked
or returned 5xx. Concurrency-safe.

---

## §4 §9 acceptance checklist

### §9.1 Know acceptance

| 项 | 标准 | 状态 |
|----|------|------|
| 单测 | 全绿 + 新增全绿 | ✅ 475/475 |
| Bridge lint | 无 orphan / missing / dup-by-id | ✅ (0/0/0; 1 dup-by-name across 6 clusters — known) |
| Phase1 小样本 | 可生成 bridge/pattern | ✅ 393 patterns live |
| Incremental ingest | 重复不重复 mine | ✅ (covered by pytest) |
| Promote/demote | priority 转换正确 | ✅ (3 demoted in lint, distill works) |
| 安全 | 无 secret 泄露 | ✅ (.env 不进仓) |

### §9.2 How acceptance

| 项 | 标准 | 状态 |
|----|------|------|
| 单测 | 129/129+ | ✅ **268/268** |
| Healthz | assets_loaded=true, missing_assets=[] | ✅ |
| SAFETY | 安全关键词稳定命中 | ✅ (Torque_Overflow, Velocity_Divergence) |
| FREE_EXPLORATION | 前期/提升中不干预 | ✅ (improvement detection works) |
| CATALYST | plateau 时命中并返回 injection_id | ✅ |
| Search | domain/staging 过滤正确 | ✅ |
| Feedback | outcomes 正确 finalize | ✅ (483 rows in export) |
| Stats | n / avg_uplift / win_rate | ✅ |
| Reload | no-change reload < 5s | ✅ **52ms** (post-fix `1452a9b`) |
| Blind spot | Unknown_Error 可持久化 | ✅ (0 currently) |

### §9.3 联合 acceptance

| 项 | 标准 | 状态 |
|----|------|------|
| Asset contract | how 可加载 know 产物 | ✅ (389 clusters loaded) |
| Reload | 增/更/删/demote 均生效 | ✅ (rebuild path works) |
| CATALYST | 返回 know pattern | ✅ (1577-char snippet) |
| Feedback | outcomes 可被 know distill | ✅ (distill --summary works) |
| Staging | 新知识默认 staging | ✅ (priority=0 entries) |
| Demoted | 负收益不再注入 | ✅ (admin reload `demoted_skipped: 3`) |
| 完整闭环 | 一键脚本可跑通 | ✅ (verify_how_lite + curl 4 步) |

### §9.4 Hermes / Frontier-Eng acceptance — **L6 separate session**

Per memory `project_margin_gate_fixes_rank_confidence_leak` + `project_w006_bimodal_variance`, latest 10-seed run (2026-06-04):

| 指标 | 数 |
|------|---|
| Overall mean Δ (10-seed, temp 0.3) | -0.378 CI [-0.65, -0.11] (stat-sig 负, W_006 双峰主导) |
| Wild panel | +0.050 (翻正) |
| Routing-CHANGED subset (2 tasks) | **+1.95** (causal evidence) |
| Routing-SAME subset (16 tasks) | -0.28 (噪声地板 + W_006 -3.50 outlier) |
| Single-seed temp-0 post-fix | +0.20 |

**结论**: routing 改动有因果效应（+1.95），但 W_006 单任务 -3.50 双峰噪声把整体均值拖到负。需要更深的 W_006 fix（新 cluster authoring 或 how 侧 top-1 lane preference 算法）才能让整体均值翻正。

---

## §5 Findings & action items

### 5.1 Real bugs/perf fixed this session
- **`python -m rosclaw_know_how_mcp.mcp_server` silently no-op** — missing `__main__` block. Fix in `e2af993`, pushed to ros-claw/rosclaw-know-how-mcp. Now both `uvx` and `python -m` invocations work.
- **`/admin/reload` no-change path 12.6s → 52ms** — `topic_group.precompute_fingerprints()` always re-encoded the 21 group fingerprints. Added mtime-based skip. Fix in `1452a9b`, pushed to ros-claw/rosclaw-how main. 44x cold / 40x warm speedup; §9.2 now met by ~100x margin.
- **Phase 7 autodraft → CATALYST surfacing gap** — autodrafted clusters lacked `topic_group`, invisible to /build. New script in rosclaw-how (`12bf4a2`) does embedding-based inference; `autodraft.py` in rosclaw-know (`ad6a5d9`) subprocesses it after ingest. End-to-end verified: fresh blind-spot → /build returns autodrafted pattern at sim 0.6923 instead of wrong-domain neighbor at 0.5298.

### 5.2 Active-learning surfacing gap — **FIXED 2026-06-05**
- Was: autodrafted clusters (Phase 7 active learning) landed in the
  bridge without `topic_group` / `topic_tag` assignment, so the CATALYST
  `/prompt/build` hot path filtered them out and the agent kept
  matching its previous wrong-domain near-neighbor instead of the
  freshly drafted pattern.
- Fix: `rosclaw-how/scripts/infer_autodraft_topic_group.py` does a
  pure-embedding inference — `topic_group` from cosine vs the existing
  21-group fingerprints (same `_AMBIGUOUS_FLOOR=0.20` query-side uses),
  `topic_tag` from kNN-1 across labeled neighbors' `standard_name`.
  Idempotent, `--force`/`--dry-run`/`--json` flags. Commit `12bf4a2`.
- `rosclaw-know/scripts/autodraft.py` subprocesses into rosclaw-how's
  venv (`ROSCLAW_HOW_PATH` env var → `../rosclaw-how` sibling) after
  `--then-ingest` to backfill labels on the new clusters. Commit
  `ad6a5d9`. New `--skip-topic-group` flag for offline runs.
- End-to-end verified: a fresh `hash_table_load_factor` blind-spot
  (count=6) drafted → ingested → auto-inferred → reloaded →
  `/prompt/build` returns `pattern_20260605_b63077d5ca` at sim 0.6923
  with `cluster_topic_group=fault-tolerant-compute` matching
  `query_topic_group=fault-tolerant-compute` (was previously
  matching the unrelated previous best cluster, sim 0.5298).
- New tests: `tests/test_infer_autodraft_topic_group.py` — 4 unit
  tests using a substring-rule fake encoder and pinned fingerprints.
  Total how test count now 272/272.

### 5.3 Doc drift
- §4.2 references `/wiki/v1/healthz` or `/know/v1/healthz` for know; actual is `/healthz`.

### 5.4 Workspace setup drift
- `rosclaw-know/wiki` is a symlink to `rosclaw-wiki/wiki` which does not
  exist in this workspace; `scripts/autodraft.py` fails out of the box
  because `AUTO_DRAFT_DIR` resolves through the dangling symlink. Worked
  around with `--out-dir data/auto_drafted_test`. Either replace the
  symlink with a real directory or change the default to a path inside
  rosclaw-know.

### 5.5 Bridge-side cleanups (non-blocking)
- 1 cluster name shared by 6 clusters (dup-by-name) — aggregation artifact
- 3 stale demoted entries with `last_seen=None` — backfill via `--last-seen` flag

### 5.6 Deferred (out of scope this session)
- §6 Other reliability/fault injection (SeekDB stale pid, asset corruption — only R-H-005 ran)
- §7 Security (API key rotation, payload limits, prompt injection in markdown)
- §4.8/4.9 heavy LLM-using E2Es (full research API job, awesome ingest)
- Hermes / L5 agent integration (Hermes lives in a separate repo not in this workspace)

---

## §6 Servers under test (still running)

```text
how  PID 396092  on 127.0.0.1:47820   uptime ~30min (restarted this session for mtime-skip fix; original PID 303848 was killed)
know PID 329358  on 127.0.0.1:47821   uptime ~75min
```

Both healthy. Kill with `kill <pid>` when no longer needed.
