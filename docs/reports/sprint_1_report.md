# Sprint 1 Report: Know curated_registry 化

**Status**: COMPLETE — committed and verified live
**Scope**: `rosclaw-know` Sprint 1 from `know-how下一步建议06-13.md`
**Know commits**: `4526933` (registry + tooling) + `5f208ad` (A_CURATED_REVIEWED tier stamp)
**HOW server**: PID `3918063` on `http://127.0.0.1:8088`, `router_backend=seekdb`, `status=ok`

---

## 1. Goal

把 `src/rosclaw_know/curated_patterns.py` 中的 15 个 hand-curated constants 迁移成可 review、可 diff、可验证的 YAML registry，同时保持向后兼容。

---

## 2. What was added

### 2.1 YAML registry

```text
rosclaw-know/data/curated_registry/
  control_loop/
    anti_windup_pid.yaml
    output_saturation_clamp.yaml
    pid_joint_latency_oscillation.yaml
  planning/
    closed_loop_replanning.yaml
  systems_compute/
    flash_attention_tiled_softmax.yaml
    simd_aes_ni_hardware_crypto.yaml
    sliding_window_kv_cache.yaml
  rl_training/
    gradient_clipping.yaml
    ppo_entropy_collapse_guard.yaml
  battery/
    multi_stage_cc_cv_fast_charging.yaml
  locomotion/
    terrain_aware_locomotion.yaml
    time_optimal_path_blending.yaml
  vision/
    motion_blur_imu_aided_deblur.yaml
  scheduling/
    metaheuristic_combinatorial_escape.yaml
  reliability/
    exponential_backoff_retry.yaml
```

每个 YAML 包含：id/title/status/runtime_eligible/source_tier/domain/robot_type/topic_group/topic_tag/safety_label/standard_name、matched_keywords（include/exclude）、log_signatures、routing_guard（positive/collateral/adversarial）、evidence、demotion、body（symptom/diagnosis/fix/anti_pattern/expected_signal/before_code/after_code/cross_domain_hints）。

### 2.2 Loader / switch

| File | Change |
|------|--------|
| `src/rosclaw_know/curated_registry.py` | Pydantic model `CuratedRegistryEntry`；`load_registry()`；`load_curated_patterns()`；`registry_enabled()` 读取 `ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED`。 |
| `src/rosclaw_know/curated_publisher.py` | `publish_curated_assets()` 现在调用 `load_curated_patterns()` 而不是直接读 `CURATED_SAFETY_PATTERNS`。默认仍走 legacy constants。 |
| `.env.example` | 新增 `ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=0`。 |
| `.gitignore` | 忽略 `data/assets_curated_from_registry/`。 |

### 2.3 Scripts

| File | Purpose |
|------|---------|
| `scripts/validate_curated_registry.py` | Schema + policy 校验：id 唯一、source_tier/status/domain 合法、topic_group/tag 非空、A/S tier 必须 ≥1 positive / ≥2 collateral、evidence 字段存在。 |
| `scripts/build_curated_from_registry.py` | 从 registry 生成 curated-only 的 `bridge_index.json` + `code_patterns/*.md`。 |
| `scripts/compare_registry_to_legacy.py` | 比较 registry 生成的 curated cluster 与 legacy bridge 中的 curated cluster，校验 routing-critical 字段语义等价。 |

### 2.4 Tests

| File | Coverage |
|------|----------|
| `tests/test_curated_registry.py` | registry 加载、legacy ↔ registry 字段映射、`load_curated_patterns()` switch、validator/build/compare 脚本端到端。 |

---

## 3. Test results

```bash
.venv/bin/python -m pytest -q
```

- **599 passed, 1 warning**（新增 11 个 registry 测试）。

```bash
.venv/bin/python scripts/validate_curated_registry.py
```

- **15 entries, OK**。

```bash
.venv/bin/python scripts/build_curated_from_registry.py --out-dir /tmp/curated_assets_from_registry
.venv/bin/python scripts/compare_registry_to_legacy.py --registry-assets /tmp/curated_assets_from_registry
```

- **15 curated patterns are semantically equivalent**。

---

## 4. Live verification

### 4.1 Publish + restart

```bash
export ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1
python -c "from rosclaw_know.curated_publisher import publish_curated_assets; print(publish_curated_assets())"
python scripts/publish_to_how.py
# restart HOW with ROSCLAW_HOW_ROUTER_BACKEND=seekdb ROSCLAW_HOW_SKIP_ASSET_LOAD=0
```

`/healthz` 返回：

```json
{
  "status": "ok",
  "router_backend": "seekdb",
  "assets_loaded": true,
  "topic_filter": {"enabled": true, "curated_topic_coverage": "15/15"},
  "outcomes_write_failures": {"count": 0}
}
```

### 4.2 Routing Panel

```bash
python scripts/verify_routing_panel.py --strict --base http://127.0.0.1:8088
```

```text
PASS=18   FAIL=0   UNREACHABLE=0   total=18
ALL PASS — routing panel cleared, paired_ab may launch.
```

T_001 仍命中 `pid_joint_latency_oscillation`（sim 0.7198），所有 collateral 任务 routing 未回退。

---

## 5. Acceptance checklist

- [x] 15 个 curated patterns 全部迁移到 YAML registry。
- [x] `validate_curated_registry.py` 通过。
- [x] `build_curated_from_registry.py` 输出与旧 bridge 语义等价。
- [x] `publish_to_how.py` 后 HOW 能 reload。
- [x] T_001 仍命中 `pid_joint_latency_oscillation`。
- [x] T_W_005 / T_W_007 / T_W_006 collateral 未被误伤。
- [x] Routing Panel 18/18 PASS。
- [x] 全量 pytest 通过。

---

## 6. Known risks

| Risk | Mitigation |
|------|------------|
| `routing_guard.collateral_queries` 部分由启发式填充，语义上不够精确。 | registry 已可人工 review / 迭代；validator 只检查数量下限，不检查语义。 |
| source_tier 从 S → A 改变 content_hash，要求 HOW reload。 | 已重启并验证 routing_panel；tier-aware ranking 默认 OFF，不影响排序。 |
| `load_curated_patterns()` 默认仍走 legacy constants。 | 显式设置 `ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1` 即可切换；.env.example 已文档化。 |

---

## 7. Decision

**Sprint 1 is complete.** The curated knowledge asset is now reviewable, diffable, and validated.

---

## 8. Next actions

1. Proceed to Sprint 2: Bridge Schema v2 and validators (`validate_bridge_schema.py`, `validate_topic_coverage.py`, `inspect_bridge_diff.py`).
2. Decide whether to enable `ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1` by default once Sprint 2 is complete.
3. Refine `routing_guard` collateral/adversarial queries based on live paired_ab data.
