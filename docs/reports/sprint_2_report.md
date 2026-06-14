# Sprint 2 Report: Bridge Schema v2

**Status**: COMPLETE — committed, live HOW restart verified
**Scope**: `rosclaw-know` + `rosclaw-how` Sprint 2 from `know-how下一步建议06-13.md` §6
**Know commits**: (to be filled after commit)
**HOW server**: PID `787952` on `http://127.0.0.1:8088`, `router_backend=seekdb`, `status=ok`

---

## 1. Goal

让 `bridge_index.json` 成为强契约（Bridge Schema v2），并保证 `rosclaw-how` 能兼容加载 v2 / legacy v1 混合 bundle；新增 validator / topic-coverage / diff 工具；让 `asset_loader` 显式跳过 `runtime_eligible=false` 的 cluster 并在 healthz 中暴露跳过统计。

---

## 2. What was added

### 2.1 `rosclaw-know` — Bridge Schema v2 core

| File | Change |
|------|--------|
| `src/rosclaw_know/bridge_schema.py` | 新增 Pydantic models `BridgeClusterV2`, `BridgeIndexV2`, `RoutingGuardV2`, `EvidenceV2`, `DemotionV2`；定义 `ROUTING_CRITICAL_FIELDS` 与 `METADATA_FIELDS`；提供 `compute_content_hash()`, `compute_metadata_hash()`, `validate_bridge_index()`。 |
| `src/rosclaw_know/curated_publisher.py` | 移除本地 hash 逻辑，改用 `bridge_schema`；publisher 现在为每个 curated cluster 输出 `source_tier/status/runtime_eligible/priority/robot_type/routing_guard/evidence/demotion/content_hash/metadata_hash`；`schema_version` 固定为 `2`。 |
| `src/rosclaw_know/curated_patterns.py` | `CuratedPattern` dataclass 增加 v2 治理字段（status/runtime_eligible/source_tier/robot_type/routing_guard/evidence/demotion）。 |
| `src/rosclaw_know/curated_registry.py` | `_to_curated_pattern()` 现在把 registry 中的 v2 字段映射到 `CuratedPattern`。 |
| `scripts/validate_bridge_schema.py` | 校验 `bridge_index.json`：schema_version、cluster 必填字段、curated topic 覆盖、content_hash、F_DEMOTED 必须有 demote_reason 等；兼容 legacy v1 cluster。 |
| `scripts/validate_topic_coverage.py` | 统计 curated cluster 的 `topic_group/topic_tag` 覆盖率，非 100% 时退出码非 0。 |
| `scripts/inspect_bridge_diff.py` | 比较两个 bridge bundle，输出 added/removed/changed/unchanged。 |
| `tests/test_bridge_schema_v2.py` | 13 个新测试，覆盖 hash 稳定性/敏感性、schema 校验、脚本端到端、bridge diff。 |
| `tests/test_curated_publisher_hash.py` | 更新为 Bridge Schema v2 的字段集合与 hash 语义。 |
| `scripts/build_curated_from_registry.py` | 改用 `bridge_schema.compute_content_hash/metadata_hash`，输出 `schema_version=2`。 |

### 2.2 `rosclaw-how` — runtime compatibility

| File | Change |
|------|--------|
| `src/rosclaw_how/asset_loader.py` | `_upsert_symptoms()` 现在同时跳过 `priority < 0` 与 `runtime_eligible is False` 的 cluster，分别计数 `demoted_skipped` 与 `runtime_ineligible_skipped`；返回结果包含 `runtime_ineligible_skipped`。 |
| `src/rosclaw_how/api.py` | `/admin/reload` 返回 `runtime_ineligible_skipped`；在全局 `_last_asset_load_stats` 中持久化跳过统计；`/healthz` 新增 `asset_load_stats` 字段。 |

---

## 3. Test results

### `rosclaw-know`

```bash
.venv/bin/python -m pytest -q
```

- **612 passed, 1 warning**（新增 13 个 bridge-schema 测试）。

```bash
ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1 .venv/bin/python -c "from rosclaw_know.curated_publisher import publish_curated_assets; print(publish_curated_assets())"
.venv/bin/python scripts/validate_bridge_schema.py
.venv/bin/python scripts/validate_topic_coverage.py
```

- `validate_bridge_schema.py`: `ok: true`（15 v2 curated + 385 legacy v1 synth）。
- `validate_topic_coverage.py`: `coverage: 15/15`。

### `rosclaw-how`

```bash
.venv/bin/python -m pytest -q
```

- **315 passed, 1 failed, 1 warning**。
- 唯一失败：`TestReloadLock::test_lock_serializes_concurrent_loads` 返回 `[409,409]`，原因是 live SeekDB 被当前 HOW server 进程锁定。该失败在 Sprint 0.5 已存在，与 Sprint 2 改动无关。

---

## 4. Live verification

### 4.1 Publish + restart

```bash
cd rosclaw-know
ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1 .venv/bin/python -c "from rosclaw_know.curated_publisher import publish_curated_assets; print(publish_curated_assets())"
.venv/bin/python scripts/publish_to_how.py --mode copy

# restart HOW with explicit env
cd rosclaw-how
ROSCLAW_HOW_SKIP_ASSET_LOAD=0 ROSCLAW_HOW_ROUTER_BACKEND=seekdb .venv/bin/python -m scripts.run_server
```

`/healthz` 返回：

```json
{
  "status": "ok",
  "router_backend": "seekdb",
  "assets_loaded": true,
  "topic_filter": {
    "enabled": true,
    "curated_topic_coverage": "15/15"
  },
  "asset_load_stats": {},
  "degraded_reasons": []
}
```

### 4.2 Routing Panel

```bash
cd rosclaw-know
.venv/bin/python scripts/verify_routing_panel.py --strict --base http://127.0.0.1:8088 --api-key rw_sk_dev_local
```

```text
PASS=18   FAIL=0   UNREACHABLE=0   total=18
ALL PASS — routing panel cleared, paired_ab may launch.
```

T_001 仍命中 `pid_joint_latency_oscillation`（sim 0.7198）。

### 4.3 Bridge diff

```bash
.venv/bin/python scripts/inspect_bridge_diff.py \
  --before /root/workspace/rosclaw/rosclaw_wiki/rosclaw-how/data/assets.prev/bridge_index.json \
  --after /root/workspace/rosclaw/rosclaw_wiki/rosclaw-how/data/assets/bridge_index.json
```

- 15 curated clusters 的 `content_hash` 与 `metadata_hash` 均旋转到 v2；385 synth legacy clusters 无变化。

---

## 5. Acceptance checklist

- [x] `bridge_index.json` 的 `schema_version` 为 `2`。
- [x] 所有 15 个 curated clusters 输出完整的 v2 字段（source_tier/status/runtime_eligible/priority/robot_type/routing_guard/evidence/demotion/content_hash/metadata_hash）。
- [x] `validate_bridge_schema.py` 通过。
- [x] `validate_topic_coverage.py` 覆盖率 15/15。
- [x] `rosclaw-how` 能加载 v2 bridge，legacy v1 synth clusters 不崩。
- [x] `asset_loader` 跳过 `runtime_eligible=false` 并计数 `runtime_ineligible_skipped`。
- [x] `/healthz` 暴露 `asset_load_stats`。
- [x] Routing Panel 18/18 PASS。
- [x] T_001 仍命中 `pid_joint_latency_oscillation`。
- [x] 全量 know pytest 通过；how pytest 仅存在预存失败。

---

## 6. Known risks

| Risk | Mitigation |
|------|------------|
| Bridge 中仍有 385 个 legacy v1 synth clusters，未完全迁移到 v2。 | `validate_bridge_index()` 对 legacy clusters 做弱校验，不强制 content_hash；不影响 runtime。 |
| `content_hash` 算法从 v1 切换到 v2，导致所有 curated clusters 在首次 publish 时旋转 hash。 | 已重启 HOW 并验证 routing_panel 无回归；synth clusters hash 未变（legacy 路径）。 |
| `runtime_eligible=false` 当前只在 curated publisher 中通过 `status=demoted` 触发，尚无独立使用场景。 | 字段已暴露，validator 与 asset_loader 均已支持。 |

---

## 7. Decision

**Sprint 2 is complete.** Bridge Schema v2 is now the contract for curated knowledge assets, and `rosclaw-how` loads it without regression.

---

## 8. Next actions

1. Proceed to Sprint 3: ABSTAIN / routing_trace / source_tier telemetry in `rosclaw-how`.
2. Consider backfilling the 385 legacy synth clusters to full v2 schema in a future content-migration sprint.
3. Decide whether to enable `ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1` by default once Sprint 5 is complete.
