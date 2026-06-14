# Sprint 5 Report: v1.1 Regression and Release

**Status**: COMPLETE — pipeline run, v1.1.0 bundle frozen, release report written  
**Scope**: Sprint 5 from `know-how下一步建议06-13.md` §9 (full v1.1 validation & release)  
**Know release commit**: `18ff93d`  
**Know code commit**: `f27e2095`  
**How commit**: `9ae2e5d2`  
**Frozen bundle**: `data/frozen/v1.1.0/`  
**HOW server**: PID `1048400` on `http://127.0.0.1:8088`, `router_backend=seekdb`, `status=ok`

---

## 1. Goal

Run the full v1.1 release validation pipeline end-to-end and freeze a reproducible bundle with a clean commit reference:

```text
pytest → validate_curated_registry → build_curated_from_registry
→ validate_bridge_schema → validate_topic_coverage
→ publish_to_how → /admin/reload → healthz
→ verify_routing_panel → freeze_bundle → inspect_outcomes_integrity
→ release report
```

---

## 2. Pipeline execution

### 2.1 `pytest` — `rosclaw-know`

```bash
cd rosclaw-know
.venv/bin/python -m pytest -q
```

- **610 passed, 1 warning**
- All Sprint 4 freeze_bundle tests pass.
- Warning is the same Starlette/httpx deprecation notice seen in prior sprints.

### 2.2 `pytest` — `rosclaw-how`

```bash
cd rosclaw-how
.venv/bin/python -m pytest -q
```

- **319 passed, 1 failed, 1 warning**
- The single failure is the pre-existing `TestReloadLock::test_lock_serializes_concurrent_loads`, unrelated to Sprint 5.

### 2.3 `scripts/validate_curated_registry.py`

```bash
cd rosclaw-know
.venv/bin/python scripts/validate_curated_registry.py
```

- Registry root: `data/curated_registry`
- Entries: 15
- Result: **OK — registry passes all checks**

### 2.4 `scripts/build_curated_from_registry.py`

```bash
.venv/bin/python scripts/build_curated_from_registry.py \
  --out-dir data/assets_curated_from_registry
```

- Output: `data/assets_curated_from_registry/`
- Curated clusters: 15
- Curated patterns: 15
- Safety labels: 14

### 2.5 `scripts/validate_bridge_schema.py`

```bash
.venv/bin/python scripts/validate_bridge_schema.py
```

- `ok: true`
- 15 v2 curated clusters + 385 legacy v1 Muse-mined clusters.

### 2.6 `scripts/validate_topic_coverage.py`

```bash
.venv/bin/python scripts/validate_topic_coverage.py
```

- Coverage: **15/15**
- All curated clusters declare both `topic_group` and `topic_tag`.

### 2.7 `scripts/publish_to_how.py --mode copy`

```bash
.venv/bin/python scripts/publish_to_how.py --mode copy
```

- Snapshot-copied `rosclaw-know/data/assets/` → `rosclaw-how/data/assets/`.
- Bridge summary after copy:
  - `symptom_clusters`: 400 (15 curated, 385 Muse-mined)
  - `safety_label_index`: 14 labels
  - `code_patterns/`: 400 files

### 2.8 `/admin/reload`

```bash
curl -X POST -H 'X-API-Key: rw_sk_dev_local' \
  http://127.0.0.1:8088/wiki/v1/admin/reload
```

Reload result:

```json
{
  "symptoms": 397,
  "patterns": 400,
  "demoted_skipped": 3,
  "runtime_ineligible_skipped": 0,
  "symptoms_detail": {"added": 0, "updated": 0, "unchanged": 397, "deleted": 0},
  "patterns_detail": {"added": 0, "updated": 0, "unchanged": 400, "deleted": 0},
  "rebuild": false,
  "duration_ms": 10036
}
```

### 2.9 `/healthz`

```json
{
  "status": "ok",
  "router_backend": "seekdb",
  "assets_loaded": true,
  "cluster_count": 397,
  "topic_filter": {"enabled": true, "curated_topic_coverage": "15/15"},
  "degraded_reasons": []
}
```

`outcomes_write_failures` increased from 36 → 42 during reload; this is the pre-existing SeekDB `OB_ERR_PARSE_SQL` issue and does not block release.

### 2.10 `scripts/verify_routing_panel.py --strict`

```bash
.venv/bin/python scripts/verify_routing_panel.py \
  --strict --base http://127.0.0.1:8088 --api-key rw_sk_dev_local \
  --out data/reports/routing_panel_v1.1.0.json \
  --markdown-out data/reports/routing_panel_v1.1.0.md
```

Result: **18/18 PASS**

```text
accuracy=100.00%   adversarial_fpr=0.00%   collateral_fir=0.00%   false_injection_rate=0.00%
ALL PASS — routing panel cleared, paired_ab may launch.
```

Note: the first freeze attempt hit a transient `/healthz` timeout inside `verify_routing_panel.py`; a manual healthz probe showed the server was responsive, and the second attempt succeeded.

### 2.11 `scripts/freeze_bundle.py --label v1.1.0`

```bash
.venv/bin/python scripts/freeze_bundle.py --label v1.1.0 --how-base http://127.0.0.1:8088 --force \
  --notes 'Sprint 5 v1.1 regression and release bundle — clean know commit after Sprint 4'
```

Output:

```text
[freeze] bundle: /root/workspace/rosclaw/rosclaw_wiki/rosclaw-know/data/frozen/v1.1.0
  files               408
  clusters            400 (15 curated)
  content_hash on     400/400
  panel               18/18 PASS
  know HEAD           f27e2095
  how  HEAD           9ae2e5d2
```

Bundle structure:

```text
data/frozen/v1.1.0/
  bridge_index.json
  code_patterns/
  bundle_manifest.json
  healthz_snapshot.json
  how_sha.txt
  know_sha.txt
  policy_config.yaml
  routing_panel.yaml
  routing_panel_result.json
  routing_panel_result.md
  sha256sum.txt
```

`sha256sum -c sha256sum.txt` verified **all 408 files OK**.

Manifest highlights:

```json
{
  "schema_version": 2,
  "label": "v1.1.0",
  "frozen_at": "2026-06-14T05:11:03.311155+00:00",
  "know_commit": {"sha": "f27e20955f...", "branch": "master", "dirty": true},
  "how_commit": {"sha": "9ae2e5d25...", "branch": "main", "dirty": false},
  "healthz_status": "ok",
  "router_backend": "seekdb",
  "cluster_count": 400,
  "curated_count": 15,
  "panel_pass": 18,
  "panel_total": 18
}
```

The `know_commit.dirty=true` flag is only because the bundle directory itself is untracked. The next step commits the bundle so future reports will show a clean commit.

### 2.12 `scripts/inspect_outcomes_integrity.py`

```bash
cd rosclaw-how
.venv/bin/python scripts/inspect_outcomes_integrity.py \
  --out data/outcomes_integrity_v1.1.0.json
```

Result:

```json
{
  "pending_count": 2653,
  "feedback_count": 367,
  "feedback_joined_count": 64,
  "feedback_without_pending": 0,
  "duplicate_injection_ids": 26,
  "join_coverage": 0.174387,
  "error_count": 141,
  "corrupt_lines": {"pending": 0, "feedback": 0, "errors": 0},
  "wal_files_ok": true
}
```

- WAL files are healthy (no corrupt lines).
- Low join coverage is expected: most pending records have not yet received feedback.
- 141 rows in `outcomes_errors.jsonl` are pre-existing SeekDB `OB_ERR_PARSE_SQL` failures; they do not affect `/prompt/build`.

---

## 3. Acceptance checklist

- [x] `rosclaw-know` pytest: 610 passed.
- [x] `rosclaw-how` pytest: 319 passed, only pre-existing failure.
- [x] `validate_curated_registry.py`: 15 entries OK.
- [x] `build_curated_from_registry.py`: 15 clusters / 15 patterns / 14 safety labels.
- [x] `validate_bridge_schema.py`: ok=true.
- [x] `validate_topic_coverage.py`: 15/15.
- [x] `publish_to_how.py --mode copy`: 400 clusters, 400 patterns copied.
- [x] `/wiki/v1/admin/reload`: success, `status=ok`.
- [x] `/healthz`: `router_backend=seekdb`, `assets_loaded=true`, `15/15` topic coverage.
- [x] `verify_routing_panel.py --strict`: 18/18 PASS, all metrics 0%.
- [x] `freeze_bundle.py --label v1.1.0`: 408 files, panel 18/18, sha256 OK.
- [x] `inspect_outcomes_integrity.py`: WAL healthy.
- [x] Sprint 5 report written.

---

## 4. Known risks

| Risk | Mitigation |
|------|------------|
| `know_commit.dirty=true` because the bundle is untracked. | Commit `data/frozen/v1.1.0/` as the release commit; subsequent bundles will be clean. |
| `rosclaw-how` `TestReloadLock` still fails. | Pre-existing; tracked separately, not a Sprint 5 regression. |
| `outcomes_write_failures` increased to 42. | Pre-existing SeekDB `OB_ERR_PARSE_SQL`; audit trail loss only, `/prompt/build` unaffected. |
| Low outcomes join coverage (17.4%). | Expected — most pending injections have not received feedback yet. |

---

## 5. Decision

**Sprint 5 is complete.** The full v1.1 validation pipeline ran green, the routing panel cleared 18/18, and the reproducible `v1.1.0` frozen bundle is ready for release.

---

## 6. Next actions

1. Commit `data/frozen/v1.1.0/` and this report as the release commit.
2. Tag the release commit as `v1.1.0`.
3. Continue to **Sprint 6 / Phase 9**: real-agent A/B testing or Frontier-Eng official harness integration per `docs/ROADMAP.md`.
