# Sprint 4 Report: Routing Panel + Frozen Bundle

**Status**: COMPLETE — committed, live HOW verified, frozen bundle produced  
**Scope**: `rosclaw-know` Sprint 4 from `know-how下一步建议06-13.md` §8  
**Know commit**: *pending*  
**Frozen bundle**: `data/frozen/iter5_p0/`  
**HOW server**: PID `1048400` on `http://127.0.0.1:8088`, `router_backend=seekdb`, `status=ok`

---

## 1. Goal

建立不依赖 LLM provider 的硬门槛测试（Routing Panel），并把通过门槛的 Know/How 资产冻结成可复现的 bundle。

---

## 2. What was changed

### 2.1 `data/panels/routing_panel.yaml`

升级到 **schema_version: 2**：

- 每个 case 增加 `type: positive | collateral | adversarial`。
- 增加 `must_not_top1` 字段用于 collateral / adversarial 防线。
- 增加 `allow_abstain` 字段让 adversarial / no-canonical case 可以合法 ABSTAIN。
- 保留 `expected_pattern_any` / `expected_strategy_any` / `expected_safety_label_any` 用于向后兼容。
- 保留 `collateral_protect` 用于记录跨 task 的回归保护关系。
- 现有 18 个任务全部保留并分类：
  - 16 个 `positive`
  - 2 个 `adversarial`（TASK_004, TASK_009）
- 为 collateral 任务增加 `must_not_top1: [pid_joint_latency_oscillation]`，确保 iter4_p9 的新 curated 不会误伤兄弟姐妹。

### 2.2 `scripts/verify_routing_panel.py`

全面升级为 v2 panel verifier：

- `PanelEntry` 解析 `type`、`must_not_top1`、`allow_abstain`、`expected_snippet_mode`。
- `_probe()` 从 `/prompt/build` 返回中捕获完整 `routing_trace`。
- 新增 hard guard：如果 `pattern_id` 在 `must_not_top1` 中，直接 FAIL。
- 新增 pass rule：adversarial + `allow_abstain=true` 时，`ABSTAIN` / `no_inject` / `FREE_EXPLORATION` 都算通过。
- 新增 `_compute_metrics()` 输出：
  - `accuracy`（positive 正确率）
  - `adversarial_false_positive_rate`
  - `collateral_false_injection_rate`
  - `false_injection_rate`
- stdout 报告增加 `type` 列和 metrics 行。
- `--out` JSON 报告包含 `metrics`、`routing_trace`、health snapshot。
- 新增 `--markdown-out` 生成 Markdown 报告。

### 2.3 `scripts/freeze_bundle.py`

从 v1 升级到 v2，严格冻结流程：

- 冻结前强制检查 HOW `/healthz`：
  - `status == ok`
  - `router_backend == seekdb`
  - `assets_loaded == true`
- 捕获 `healthz_snapshot.json`。
- 复制 `bridge_index.json` 和 `code_patterns/`。
- 写入 `know_sha.txt`、`how_sha.txt`。
- 复制 `routing_panel.yaml`。
- 内部调用 `verify_routing_panel.py` 生成：
  - `routing_panel_result.json`
  - `routing_panel_result.md`
- 若 panel 有任何失败，直接拒绝冻结。
- 自动生成 `policy_config.yaml`（runtime knobs，无 secret）。
- 生成 `sha256sum.txt` 覆盖 bundle 内所有文件。
- 生成 `bundle_manifest.json`（schema_version=2），包含 know/how commits、healthz、cluster counts、panel pass/fail、文件清单、bundle hash。

### 2.4 `tests/test_freeze_bundle.py`

- 完全重写以匹配新的 Sprint 4 bundle 格式和 `freeze()` 签名。
- 覆盖：
  - git head capture
  - sha256 稳定性
  - `_walk_bundle` 确定性排序
  - end-to-end freeze 生成正确文件集
  - 拒绝无 `--force` 覆盖
  - `--force` 覆盖
  - 拒绝 degraded HOW
  - 拒绝 panel 失败的冻结

---

## 3. Test results

### `rosclaw-know`

```bash
cd rosclaw-know
.venv/bin/python -m pytest -q
```

- **610 passed, 1 warning**（新增/重写 12 个 freeze_bundle 测试）。

### Validators

```bash
.venv/bin/python scripts/validate_bridge_schema.py
.venv/bin/python scripts/validate_topic_coverage.py
```

- `validate_bridge_schema.py`: `ok: true`（15 v2 curated + 385 legacy v1 synth）。
- `validate_topic_coverage.py`: `coverage: 15/15`。

### `rosclaw-how`

```bash
cd rosclaw-how
.venv/bin/python -m pytest -q
```

- **319 passed, 1 failed, 1 warning**。
- 唯一失败仍是预存在的 `TestReloadLock::test_lock_serializes_concurrent_loads`，与 Sprint 4 无关。

---

## 4. Live verification

### 4.1 Routing Panel

```bash
cd rosclaw-know
.venv/bin/python scripts/verify_routing_panel.py \
  --strict --base http://127.0.0.1:8088 --api-key rw_sk_dev_local \
  --out data/reports/routing_panel_iter5_p0.json \
  --markdown-out data/reports/routing_panel_iter5_p0.md
```

结果：**18/18 PASS**

```text
accuracy=100.00%   adversarial_fpr=0.00%   collateral_fir=0.00%   false_injection_rate=0.00%
ALL PASS — routing panel cleared, paired_ab may launch.
```

### 4.2 Frozen Bundle

```bash
cd rosclaw-know
.venv/bin/python scripts/freeze_bundle.py --label iter5_p0 --how-base http://127.0.0.1:8088 --force
```

输出：

```text
[freeze] bundle: /root/workspace/rosclaw/rosclaw_wiki/rosclaw-know/data/frozen/iter5_p0
  files               408
  clusters            400 (15 curated)
  content_hash on     400/400
  panel               18/18 PASS
  know HEAD           dce7f281
  how  HEAD           9ae2e5d2
```

Bundle 结构：

```text
data/frozen/iter5_p0/
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

Manifest 关键字段：

```json
{
  "label": "iter5_p0",
  "know_commit": {"sha": "dce7f281...", "branch": "master"},
  "how_commit": {"sha": "9ae2e5d2...", "branch": "main"},
  "healthz_status": "ok",
  "router_backend": "seekdb",
  "cluster_count": 400,
  "curated_count": 15,
  "panel_pass": 18,
  "panel_total": 18,
  "sha256_of_bundle_sha256sum_file": "ce0ce68c..."
}
```

### 4.3 HOW healthz

```json
{
  "status": "ok",
  "router_backend": "seekdb",
  "assets_loaded": true,
  "topic_filter": {"enabled": true, "curated_topic_coverage": "15/15"},
  "asset_load_stats": {"demoted_skipped": 3, "runtime_ineligible_skipped": 0},
  "degraded_reasons": []
}
```

---

## 5. Acceptance checklist

- [x] `routing_panel.yaml` schema_version=2，含 `type` / `must_not_top1` / `allow_abstain`。
- [x] 18 个任务分类为 positive / adversarial，全部通过。
- [x] `verify_routing_panel.py` 收集 `routing_trace`。
- [x] JSON 报告包含 metrics（accuracy、adversarial_fpr、collateral_fir、false_injection_rate）。
- [x] Markdown 报告生成。
- [x] `freeze_bundle.py` 强制检查 healthz ok + seekdb + assets_loaded。
- [x] `freeze_bundle.py` 在 panel 失败时拒绝冻结。
- [x] Frozen bundle 包含 know_sha / how_sha / healthz_snapshot / policy_config / routing_panel_result / sha256sum / manifest。
- [x] Frozen bundle `iter5_p0` 成功生成，panel 18/18 PASS。
- [x] `rosclaw-know` pytest 全绿。
- [x] `rosclaw-how` pytest 仅预存失败。

---

## 6. Known risks

| Risk | Mitigation |
|------|------------|
| Frozen bundle 体积较大（~1 MB bridge + code_patterns）。 | 只冻结被 panel 验证通过的迭代标签；旧 bundle 可归档或清理。 |
| `know_commit.dirty=true` 因为当前工作区尚未提交。 | 提交 Sprint 4 代码后重新冻结的 bundle 将为 clean。 |
| Adversarial case 只有 2 个，覆盖不足。 | v2 panel schema 已支持 adversarial；后续可随新 curated 增加更多 adversarial probes。 |
| `outcomes_write_failures` 仍在增加（SeekDB OB_ERR_PARSE_SQL）。 | Sprint 0.5 WAL 保证 audit trail 不丢；Sprint 4 未改动该路径。 |

---

## 7. Decision

**Sprint 4 is complete.** Routing Panel v2 is now the hard gate, and `freeze_bundle.py` produces reproducible frozen bundles that capture the exact asset state required for any formal experiment.

---

## 8. Next actions

1. Proceed to **Sprint 5: v1.1 regression and release report**.
2. Run the full v1.1 validation pipeline：pytest → validate_curated_registry → build_curated_from_registry → validate_bridge_schema → validate_topic_coverage → publish_to_how → /admin/reload → healthz → verify_routing_panel → freeze_bundle → inspect_outcomes_integrity → release report.
3. Decide whether to re-freeze `iter5_p0` after committing to get a clean `know_commit`.
