下面我先给一个直接判断：

**你们现在遇到的不是普通工程 bug，而是 know-how 系统从“能证明有效”走向“继续可扩展提升”时必然遇到的三重瓶颈：**

1. **LLM 知识天花板**：很多任务 baseline LLM 已经 9.7/10、10/10，继续注入 curated 知识没有空间。
2. **路由结构天花板**：how 侧 5 层策略已经把“该救的路由”基本救完了，再调 similarity / margin 的收益会越来越小。
3. **评测方法天花板**：n=10、小面板、LLM judge、server 非确定性、reload 非幂等，会把真实信号和噪声混在一起。

所以后续优化的核心，不是继续盲目加 pattern，也不是继续调 `_CURATED_PREFERENCE_MARGIN`。下一阶段应该从：

> **“手工 curated + 路由调参”**

升级为：

> **“任务空间建模 + headroom 识别 + 证据驱动内容生产 + 可复现实验闭环 + 官方 verifier 验证”。**

---

# 一、当前状态判断：不是失败，而是第一阶段已经到顶

## 1.1 当前结果其实已经不错

know 报告显示，当前 shipped 状态是 **14 个手工 curated 模式 + 385 个 Muse 合成簇，共 399 个 symptom_cluster**；n=30 对比实验中，ALL 平均提升 **+0.265**，HOME 提升 **+0.420**，并且 ALL/HOME 的 t-stat 都超过 5，CI 远离 0。非平手胜率是 **73.9%**。这说明 know-how 不是“没用”，而是已经在 home-turf 上形成了稳定正收益。

how 侧也不是简单检索器了。报告里明确它已经形成了 **5 层策略栈**：state router、software-resource fall-through、synth quality gate、curated rescue/preference、adaptive snippet mode；而且不引入 per-task vocabulary，只改变“哪个 cluster 胜出”和“以什么 snippet 形态注入”。

这意味着第一阶段目标已经达成：

```text
证明 know-how 可以有效；
证明 curated pattern 比纯 Muse pattern 更可靠；
证明 how 侧策略能把错误路由压下去；
证明 HOME 面板上能稳定提升。
```

---

## 1.2 真正的问题：继续优化的方向变了

当前最关键的数据不是 ALL +0.265，而是下面三组现象。

### 现象一：WILD 基本卡死

WILD n=30 只有 **+0.071**，CI 是 **[-0.02, +0.16]**，没有显著越过 0；报告进一步指出 5 个 wild cold spots 里有 4 个 baseline LLM 已经接近满分，只有 T_W_002 还有明显空间。 

这说明 WILD 不是简单“覆盖不够”，而是：

```text
很多所谓 cold spot，其实对当前 LLM 已经不冷。
```

如果 baseline 已经 9.7/10 或 10/10，再加 curated pattern 不会提升，只会增加噪声和误导风险。

---

### 现象二：正收益集中在少数路由改变任务

how 报告指出，策略栈只真正改变了 **8/30 个任务** 的路由，其余 22 个任务 baseline 和 d07ddac shipped state 路由相同，对 A/B Δ 的贡献基本是噪声。

这说明后续不能只看整体均值，要看：

```text
routing changed subset
routing same subset
targeted pattern subset
non-targeted noise subset
```

否则你可能以为某个改动有效，实际上只是 control-side LLM 漂移。

---

### 现象三：有些 curated 反而稳定拖后腿

know 报告把任务分成几类，其中 T_003、T_W_005、T_W_006 是 **稳定小回退**，原因是 curated 注入引入了和 LLM 自然偏好不同的解法，反而拖低结果。

这非常关键。它说明 know-how 不是“注入越多越好”，而是应该学会：

```text
该注入时注入；
不该注入时 abstain；
不确定时轻量提示；
高置信 curated 才 full snippet；
负迁移任务要禁用或降权。
```

---

# 二、根因分析：你们卡住的不是一个问题，而是 6 个耦合问题

## 2.1 内容瓶颈：curated pattern 的边际收益递减

最初 7 个 curated → 14 个 curated 带来了明显提升；但 iter5 想通过 augment `anti_windup_pid` 的 standard_name 继续提 T_W_007，结果 targeted gain 是 0，因为 T_W_007 baseline 已经 10/10 saturate，最终 paired 结果还变成负收益并被 revert。

这说明：

```text
下一批 curated 不能靠直觉写；
必须先做 headroom probe；
baseline 已经 ≥9.7 的任务不允许 author curated；
只给 “baseline 不会自然答出 canonical fix” 的任务写 curated。
```

---

## 2.2 路由瓶颈：how 的策略调参已经进入微收益区

当前 how 侧已经做了非常细的策略：

* software-resource SAFETY fall-through；
* non-curated top-1 必须满足 `sim ≥ 0.60` 和 margin ≥ 0.015；
* curated rescue/preference；
* domain lock；
* curated similarity floor；
* curated full snippet、synth lightweight snippet。

这已经不是粗糙路由了。继续调 `0.60 / 0.015 / 0.15` 这些数字，收益会很小，风险反而变大。

后续要换成：

```text
从 similarity-only / provenance-only
升级到 task-family + domain + headroom + historical uplift + negative-transfer 的 contextual policy。
```

---

## 2.3 评测瓶颈：n=10 已经不够

报告明确指出，n=10 的方差预算不够支撑 paired 比较；iter5 的负结果很大程度来自 16 个没有改变路由的任务的 control-side 噪声漂移。

这意味着后续任何“内容级改动”都必须：

```text
targeted rows 先测；
routing-changed rows 单独测；
n=30 起步；
高方差任务做 paired bootstrap；
不能跨 server restart 比较。
```

---

## 2.4 工程瓶颈：reload 非幂等破坏可复现

how 报告指出 `/admin/reload` 非幂等：同一 server PID 上 edit → reload → probe → revert → reload，多轮后 live-probe 会和 startup-fresh state 不一致；可能原因包括 metadata 更新但 embedding 不更新、fingerprint tie-break 改变、model warm cache 泄漏。

know 报告也提到同一 server PID 上 reload 2+ 次后 ANN ranking 可能漂移，建议 iter 间完整 restart，不要 hot reload。

这是 P0 级问题。只要 reload 不可复现，所有小幅路由优化都不可信。

---

## 2.5 Schema 瓶颈：domain/source/content_hash 太弱

how 侧指出 `domain` 是 rescue 的 domain lock 关键输入，但现在还是 free-string，大小写或命名漂移会静默破坏 rescue；`source` 也只有 curated / 非 curated 二元信号，无法表达 reviewed、autodraft、Muse、trajectory-derived 等不同可信度等级。

这说明你们现在缺少：

```text
强类型 schema；
发布时 validator；
source tier；
provenance；
content_hash 语义规范；
routing-critical field 变更强制 re-embed。
```

---

## 2.6 评价目标瓶颈：现在更像“LLM judge 面板”，还不是完整 Frontier-Eng verifier 闭环

当前报告的 n=30 面板非常适合做 **know-how 路由和提示策略回归测试**，但它还不是最终的 Frontier-Eng 官方 benchmark 证明。Frontier-Eng 本身强调的是固定预算下 propose → execute → evaluate → revise，candidate 必须在 frozen verifier / read-only evaluator 下得到真实分数，而不是自报或仅靠 LLM judge。

所以下一阶段应该把当前面板定位为：

```text
Know-How Routing Regression Harness
```

而不是最终 benchmark harness。

最终要走：

```text
Hermes / OpenEvolve / Frontier-Eng official verifier
→ real executable artifacts
→ true evaluator score
→ know-how trace
→ official rank/win-rate analysis
```

---

# 三、总体优化路线：从“策略调参”转向“证据驱动的知识生产系统”

我建议后续分成 6 条主线：

```text
A. 实验可复现基础设施：先把 reload、bridge、judge、server 漂移压住
B. Bridge Schema v2：domain/source/provenance/content_hash 强类型化
C. Headroom-First Curated Authoring：只给有提升空间的任务写知识
D. Contextual Routing v2：从 similarity 改为 task-aware + evidence-aware
E. Evidence Trace v2：证明 agent 是否真的用了 hint
F. Frontier-Eng Official Harness：从 LLM judge 面板走向真实 verifier 闭环
```

---

# 四、P0：先修实验与工程地基

## 4.1 修复 `/admin/reload` 非幂等

### 问题

当前 reload 后 ANN ranking 会漂移，尤其 similarity 差距 0.001～0.01 的 top-K，足以改变 curated preference / rescue 分支。

### 目标

```text
同一个 bridge_index + 同一个 code_patterns + 同一个 server restart
→ 任意次数 reload
→ top-K routing 完全一致
```

### 实施方案

#### 4.1.1 content_hash 必须覆盖所有 routing-critical 字段

当前怀疑是 topic_group / topic_tag / domain 等字段变化没有触发 embedding upsert。建议 know 侧统一生成：

```python
ROUTING_CRITICAL_FIELDS = [
    "standard_name",
    "domain",
    "topic_group",
    "topic_tag",
    "matched_keywords",
    "cross_domain_analogies",
    "associated_patterns",
    "source",
    "source_tier",
    "priority",
    "snippet_mode_hint",
]
```

content_hash：

```python
def compute_cluster_content_hash(cluster: dict) -> str:
    payload = {
        k: normalize(cluster.get(k))
        for k in ROUTING_CRITICAL_FIELDS
    }
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
```

验收：

```text
修改 domain → hash 改变
修改 topic_group → hash 改变
修改 source_tier → hash 改变
修改 priority → hash 改变
仅修改 uplift_n → 可选，不一定 re-embed，但 metadata 必须 update
```

#### 4.1.2 how reload 强制三段式

```text
1. Load bridge into memory
2. Compute desired index snapshot
3. Atomic swap SeekDB collection / or full upsert with deterministic order
```

关键点：

```python
clusters = sorted(bridge["symptom_clusters"].items(), key=lambda x: x[0])
```

任何 upsert、delete、fingerprint precompute 都必须按稳定排序。

#### 4.1.3 reload 后做 self-probe

建立 `data/assets/routing_canary.json`：

```json
[
  {
    "name": "pid_antiwindup",
    "query": "PID integral windup actuator saturation overshoot",
    "expected_top1_any": ["anti_windup_pid", "output_saturation_clamp"],
    "min_similarity": 0.60
  },
  {
    "name": "flash_attention_oom",
    "query": "flash attention cuda out of memory tiled softmax",
    "expected_top1_any": ["flash_attention_tiled_softmax"]
  }
]
```

reload 后自动跑 canary：

```text
if canary fail:
  reload 返回 409
  router 不切换到新 index
```

### 验收标准

```text
同一 bridge 连续 reload 10 次，top-5 排序完全一致。
edit → reload → revert → reload 后，结果等于 fresh restart。
routing_canary 全部通过。
```

---

## 4.2 冻结实验资产，禁止“边写边测”

报告已经指出 bridge_index 多次 publish 后有微移，严格 bit-reproduce 历史 iter 数据变得困难。

### 实施方案

每次实验必须生成：

```text
data/frozen/
  iter4_d07ddac/
    bridge_index.json
    code_patterns/
    how_commit.txt
    know_commit.txt
    eval_panel.yaml
    model_config.yaml
    routing_canary.json
    sha256sum.txt
```

实验只允许从 frozen bundle 启动：

```bash
rosclaw-how --assets data/frozen/iter4_d07ddac
```

禁止直接用 live symlink 做严肃实验。

### 验收标准

```text
任何实验报告必须写入 frozen_bundle_id。
没有 frozen_bundle_id 的结果不进入主报告。
```

---

## 4.3 评测 harness 改成 server-PID 内交替 paired

报告指出 302.ai 服务端非确定性会导致同 seed 同 prompt 在不同 server 实例上分差 ±0.5～1.5，污染 paired 比较。

### 实施方案

禁止：

```text
先跑全部 control，再跑全部 treatment；
control 和 treatment 跨 server restart；
```

必须：

```text
for seed in seeds:
  for task in randomized_tasks:
    run control
    run treatment
    run placebo
    run shuffled
```

并记录：

```json
{
  "server_pid": "...",
  "model_endpoint": "...",
  "model_name": "...",
  "request_hash": "...",
  "response_hash": "...",
  "seed": 12,
  "task": "T_W_006"
}
```

### 验收标准

```text
同一 task/seed 的 control/treatment 必须在同一 server PID 内完成。
跨 server PID 的结果只能做 exploratory，不进入 paired 主表。
```

---

# 五、P1：Bridge Schema v2，先把知识资产变成“可治理资产”

当前 `source: curated` 是 how 策略的唯一高置信信号，domain 又是 free-string，这已经不够。

## 5.1 新 bridge schema

每个 cluster 增加：

```json
{
  "schema_version": 2,
  "cluster_id": "cluster_x",
  "standard_name": "...",
  "domain": "control_locomotion",
  "topic_group": "pid_control",
  "topic_tags": ["anti_windup", "saturation", "overshoot"],
  "source_tier": "S_CURATED",
  "source_kind": "curated | muse | autodraft | reviewed | trajectory | benchmark",
  "provenance": {
    "created_by": "human | muse | trajectory_miner",
    "created_at": "2026-06-09T...",
    "curator": "shaoxiang007",
    "source_refs": [],
    "last_reviewed_at": "...",
    "review_status": "draft | reviewed | verified | demoted"
  },
  "routing": {
    "preferred_snippet_mode": "full",
    "min_similarity_override": null,
    "allowed_task_families": [],
    "blocked_task_families": [],
    "known_negative_tasks": ["T_W_006"]
  },
  "evidence": {
    "n": 30,
    "win_rate": 0.73,
    "avg_delta": 0.42,
    "ci95": [0.27, 0.57],
    "last_panel": "frontier_kh_n30_iter4"
  },
  "content_hash": "..."
}
```

---

## 5.2 domain enum

建议统一为小写枚举：

```python
Domain = Literal[
    "control_locomotion",
    "planning_decision",
    "systems_compute",
    "learning_training",
    "perception_vision",
    "memory_reasoning",
    "world_physics",
    "operations_research",
    "energy_storage",
    "robotics_manipulation"
]
```

发布时 validator：

```python
assert cluster["domain"] in DOMAIN_ENUM
```

禁止：

```text
Control_Locomotion
control
controls
```

全部迁移成：

```text
control_locomotion
```

---

## 5.3 source_tier 设计

把二元 curated / non-curated 改成多级：

| source_tier          | 含义                      | how 默认策略                  |
| -------------------- | ----------------------- | ------------------------- |
| `S_CURATED_VERIFIED` | 人工 curated + n≥30 验证正收益 | full，可 preference         |
| `A_CURATED_REVIEWED` | 人工 curated，但样本不足        | full，但不强 preference       |
| `B_TRAJECTORY_MINED` | 从真实成功轨迹挖掘               | lightweight/full adaptive |
| `C_MUSE_SYNTH`       | Muse 生成                 | lightweight，需高 sim/margin |
| `D_AUTODRAFT`        | blind spot 自动起草         | staging，仅低剂量              |
| `F_DEMOTED`          | 负收益                     | 不注入                       |

这样 how 不再只用 `source == curated`，而是用：

```python
if source_tier.startswith("S_"):
    curated_like = True
elif source_tier.startswith("A_"):
    reviewed_like = True
else:
    synth_like = True
```

---

## 5.4 schema validator

新增：

```bash
python scripts/validate_bridge_schema.py
```

检查：

```text
domain enum
source_tier enum
content_hash present
associated_patterns exists
pattern markdown exists
routing fields legal
evidence fields type legal
known_negative_tasks task id legal
```

CI 门禁：

```text
validate_bridge_schema 不通过，禁止 publish_to_how。
```

---

# 六、P1：Headroom-First Curated Authoring，不再盲写 pattern

这是后续提升的核心。

## 6.1 建立 task headroom map

每个候选任务先跑 baseline probe：

```text
control-only × n=10 或 n=30
```

输出：

```json
{
  "task": "T_W_002 GradExplosionRL",
  "control_mean": 9.20,
  "control_std": 0.94,
  "headroom": 0.80,
  "saturation": false,
  "authoring_allowed": true
}
```

规则：

| baseline mean | 动作                       |
| ------------: | ------------------------ |
|         ≥ 9.7 | 禁止写 curated，标记 saturated |
|       9.0–9.7 | 谨慎，要求 strong hypothesis  |
|       7.0–9.0 | 允许 curated               |
|         < 7.0 | 优先 curated / research    |

报告里明确建议“决定加 curated 前，先用 control-only 跑 baseline；C̄ ≥ 9.7 就放弃”。

---

## 6.2 curated authoring pipeline

每个新 curated 必须走 7 步：

```text
1. Headroom probe
2. Failure diagnosis
3. Pattern draft
4. Routing dry-run
5. Targeted n=30 A/B
6. Non-target regression check
7. Promote to source_tier=S_CURATED_VERIFIED
```

### Step 1：Headroom probe

```bash
python scripts/probe_headroom.py \
  --panel wild_expanded.yaml \
  --n 30 \
  --mode control_only
```

输出：

```text
candidate_headroom.csv
```

### Step 2：Failure diagnosis

对有 headroom 的任务，抽 10 个失败样本：

```text
baseline wrong answers
baseline incomplete fixes
baseline hallucinated mechanisms
```

总结：

```yaml
task: T_W_002
failure_mode: RL gradient explosion
baseline_gap:
  - mentions lower lr but misses gradient clipping
  - misses reward normalization
  - misses advantage normalization
canonical_fix:
  - clip_grad_norm_
  - normalize advantages
  - reduce learning rate
  - monitor NaN and entropy
```

### Step 3：Pattern draft

新 pattern 必须写成 action-oriented：

```markdown
## Symptom
RL training loss or policy gradient suddenly explodes...

## Diagnosis
The update step is unstable because gradient norm / advantage scale is unbounded...

## Fix
1. Add gradient norm clipping.
2. Normalize advantages per batch.
3. Reduce learning rate only after clipping is in place.

## Code Target
Look for optimizer.step(), loss.backward(), advantage computation.

## Anti-pattern
Do not only reduce learning rate while leaving unbounded advantages.

## Expected Signal
NaNs disappear; reward curve becomes monotonic; entropy does not collapse abruptly.
```

### Step 4：Routing dry-run

```bash
python scripts/probe_routing.py \
  --query-set task_w002_queries.yaml \
  --top-k 10 \
  --expect pattern_rl_gradient_explosion
```

检查：

```text
target task top-1/top-3 命中；
non-target 任务不误命中；
T_W_004 PPO entropy 不被抢；
gradient_clipping 原 pattern 不冲突。
```

### Step 5：Targeted n=30

只跑目标任务：

```bash
python scripts/run_paired_ab.py \
  --tasks T_W_002 \
  --n 30 \
  --control no_kh \
  --treatment kh_new_pattern
```

准入：

```text
target Δ ≥ +0.30
CI 下界 > 0 或 paired bootstrap positive probability ≥ 0.9
```

### Step 6：Non-target regression

跑与它容易混淆的任务：

```text
T_W_004 EntropyCollapsePPO
T_W_007 IntegrationWindup
T_001 PIDTuning
Learning_Training 相关任务
```

准入：

```text
non-target mean Δ ≥ -0.05
不能引入稳定负迁移
```

### Step 7：Promote

满足条件才：

```text
source_tier: S_CURATED_VERIFIED
review_status: verified
```

否则：

```text
source_tier: A_CURATED_REVIEWED
review_status: draft
how 不允许 curated preference，只能 normal retrieval。
```

---

# 七、P1：对现有 14 个 curated 做“正负迁移治理”

你们现在不应该只加新 pattern，还要治理已有 pattern。

## 7.1 按任务贡献分类

基于 n=30 表：

### 稳定赢家：保留并强化

```text
T_002 QuadrupedGait +1.30
T_005 AES128 +0.77
T_008 JobShop +1.30
T_W_002 GradExplosionRL +0.77
T_001 PIDTuning +0.33
T_010 UAVInspection +0.40
T_W_004 EntropyCollapsePPO +0.33
```

### 微小贡献：保持但降低注入强度

```text
T_004 HighReliableSimulation +0.03
T_007 BatteryFastCharging +0.07
T_009 TopologyOptimization +0.10
T_W_001 KVCacheLongContext +0.03
```

### 负收益：必须加 blocked_task / downgrade

```text
T_003 RobotArmCycleTime -0.10
T_W_005 ActuatorOvershoot -0.27
T_W_006 PlanningDivergence -0.30
```

报告已经明确这些负收益来自 curated 注入和 LLM 自然偏好不一致。

---

## 7.2 给负收益任务加禁用规则

在 cluster metadata 里加：

```json
"routing": {
  "known_negative_tasks": ["T_W_006"],
  "blocked_task_families": ["planning_divergence_long_horizon"],
  "max_snippet_mode": "lightweight",
  "abstain_if_control_mean_ge": 9.7
}
```

how 侧：

```python
if req.task_name in pattern.known_negative_tasks:
    skip_or_lightweight(pattern)
```

对 T_W_005、T_W_006，不要马上删 pattern，而是让 how 学会：

```text
这个 pattern 对某些任务有效；
但对这些 task signature 负迁移。
```

---

# 八、P1：Contextual Routing v2

当前 how 主要是 provenance-driven + retrieval-rank-driven。下一步要变成：

```text
provenance + retrieval + task_context + evidence + headroom + negative-transfer
```

## 8.1 新 ranking score

```python
final_score = (
    0.35 * semantic_similarity
  + 0.15 * topic_group_match
  + 0.10 * domain_match
  + 0.10 * source_tier_score
  + 0.15 * evidence_score
  + 0.05 * headroom_score
  - 0.20 * negative_transfer_penalty
  - 0.10 * saturation_penalty
  - 0.10 * recent_pattern_penalty
)
```

### evidence_score

```python
evidence_score = clamp(
    0.5 * win_rate
  + 0.3 * normalized_delta
  + 0.2 * ci_confidence,
  0, 1
)
```

### negative_transfer_penalty

```python
if task_name in pattern.known_negative_tasks:
    penalty = 1.0
elif task_family in pattern.blocked_task_families:
    penalty = 0.7
else:
    penalty = 0
```

### saturation_penalty

```python
if task_headroom.control_mean >= 9.7:
    penalty = 1.0
```

---

## 8.2 how 请求里必须加入 task context

当前 CATALYST query 已经改成优先用 rich error_log，而不是短 label。 但还不够。建议 v2 请求：

```json
{
  "task_context": {
    "task_name": "T_W_006",
    "task_family": "planning_divergence",
    "domain": "planning_decision",
    "artifact_type": "python",
    "objective_direction": "maximize",
    "headroom_mean": 9.7
  },
  "error_log": "...",
  "previous_scores": [9.7, 9.7, 9.8],
  "current_iteration": 4,
  "recent_pattern_ids": ["closed_loop_replanning"]
}
```

旧 API 保持兼容，但内部 adapter 转成 v2。

---

## 8.3 增加 ABSTAIN 策略

现在 how 返回 empty snippet 但语义上不够清楚。建议新增：

```text
ABSTAIN_HIGH_BASELINE
ABSTAIN_LOW_CONFIDENCE
ABSTAIN_NEGATIVE_TRANSFER
```

返回：

```json
{
  "strategy": "ABSTAIN",
  "abstain_reason": "high_baseline_saturation",
  "injected": false,
  "control_mean": 9.8
}
```

这样报告能区分：

```text
没找到知识；
找到了但低置信；
找到了但任务已饱和；
找到了但历史负迁移。
```

---

# 九、P2：Evidence Trace v2，证明“真的用了知识”

当前报告能证明 KH vs no KH 有分数差，但还不能充分证明：

```text
某个 pattern → 某个 snippet → LLM 采用了其中机制 → 输出变好
```

## 9.1 trace schema

每次 how 调用记录：

```json
{
  "trace_id": "kh_trace_x",
  "run_id": "n30_iter4_seed12",
  "task_id": "T_002",
  "query_hash": "...",
  "strategy": "CATALYST",
  "pattern_id": "terrain_aware_locomotion",
  "source_tier": "S_CURATED_VERIFIED",
  "similarity": 0.72,
  "routing_features": {
    "semantic": 0.72,
    "domain_match": 1.0,
    "evidence_score": 0.8,
    "negative_transfer_penalty": 0
  },
  "snippet_mode": "full",
  "snippet_hash": "...",
  "llm_output_hash": "...",
  "mechanism_used": true,
  "mechanism_features": [
    "terrain-aware foot clearance",
    "stability over speed"
  ],
  "judge_score_control": 8.67,
  "judge_score_treatment": 9.97,
  "delta": 1.30
}
```

---

## 9.2 mechanism_used 自动判定

针对 curated pattern，手写 feature detectors：

| pattern                              | feature                                            |
| ------------------------------------ | -------------------------------------------------- |
| `anti_windup_pid`                    | anti-windup、integral clamp、conditional integration |
| `flash_attention_tiled_softmax`      | tiled softmax、shared memory、online softmax         |
| `terrain_aware_locomotion`           | foot clearance、terrain adaptation、stability gait   |
| `metaheuristic_combinatorial_escape` | tabu / simulated annealing / local search escape   |
| `multi_stage_cc_cv_fast_charging`    | CC-CV、SOC switch、thermal/plating constraint        |
| `gradient_clipping`                  | clip_grad_norm、advantage normalization             |

输出：

```text
hint_use_rate
mechanism_use_rate
mechanism_success_rate
```

这比单纯看 score 更能说明 know-how 是否被 agent 理解。

---

# 十、P2：把当前 n=30 面板扩展成 “KH Regression Benchmark”

当前 18 任务面板很好，但 WILD 只有 8 个，且多个已饱和。报告也指出 wild 集应该扩到 30+，才能进一步收紧 wild CI。

## 10.1 面板重构

建议建立 4 个面板：

### Panel A：Home Regression Panel

现有 10 个 home 任务，保持不动，用于防回归。

### Panel B：Wild Headroom Panel

新增 30 个非 home 任务，但要求 baseline mean < 9.5。

### Panel C：Negative Transfer Panel

专门收集容易被 curated 误伤的任务，例如：

```text
RobotArmCycleTime
ActuatorOvershoot
PlanningDivergence
所有曾经 Δ < -0.1 的任务
```

### Panel D：Saturation Panel

baseline ≥ 9.7 的任务，用于测试 ABSTAIN：

```text
T_W_001
T_W_007
T_W_008
...
```

目标不是提升，而是：

```text
how 应该不注入或轻量注入。
```

---

## 10.2 每次 PR 必跑的 gating

| 面板                |  n | 目标             |
| ----------------- | -: | -------------- |
| Home              | 10 | 不回退            |
| Negative Transfer | 10 | 不恶化            |
| Saturation        |  5 | abstain 正确     |
| Targeted          | 30 | 新 pattern 必须显著 |

PR 通过标准：

```text
Home mean Δ 不低于 shipped baseline -0.05
Negative panel mean Δ ≥ -0.05
Saturation panel injection_rate < 20%
Targeted panel Δ > 0 且 CI 下界尽量 > 0
```

---

# 十一、P2：从 LLM judge 面板走向 Frontier-Eng 官方 verifier

Frontier-Eng 的本质是可执行 artifact + frozen verifier + hard feasibility constraints；它的评价完整性依赖 read-only evaluator、verifier-parsed scoring、sandboxed candidate execution。

所以最终 know-how 必须在 official verifier 中证明。

## 11.1 两套评测分工

| Harness                       | 目的           | 是否官方分数 |
| ----------------------------- | ------------ | ------ |
| KH Routing Regression Panel   | 快速测路由/prompt | 否      |
| Frontier-Eng Official Harness | 测真实工程优化收益    | 是      |

当前 n=30 面板归入第一类，不要混淆。

---

## 11.2 Official Harness 流程

```text
for task in Frontier-Eng 47:
  baseline agent
  hermes_no_kh
  hermes_kh
  hermes_kh_abstain
  hermes_placebo
  hermes_shuffled
```

每轮：

```text
1. 读取 editable artifact
2. Hermes 生成 patch
3. 执行官方 verifier
4. 解析 valid + raw_score
5. normalize score direction
6. plateau 时调用 how
7. 记录 injection trace
8. feedback 回 how
9. best feasible score 计入最终
```

---

## 11.3 指标

```text
Average Rank
Pairwise Win Rate
Win Rate over Initial
Best Feasible Score
Valid Rate
Post-Injection Delta@1/3/5
Hint Use Rate
Abstain Accuracy
Negative Transfer Rate
```

正式结论只允许基于：

```text
same task
same seed
same budget
same model
same verifier
paired comparison
```

---

# 十二、P3：Know 内容生产系统升级

## 12.1 从 “curated_patterns.py 手写列表” 升级为 curated registry

现在 14 个 curated 定义在 `CURATED_SAFETY_PATTERNS`。 这个阶段够用，但后续需要治理。

建议改成：

```text
data/curated_registry/
  anti_windup_pid.yaml
  flash_attention_tiled_softmax.yaml
  terrain_aware_locomotion.yaml
```

每个 yaml：

```yaml
id: flash_attention_tiled_softmax
domain: systems_compute
topic_group: cuda_attention
source_tier: S_CURATED_VERIFIED
status: verified
matched_keywords:
  - flashattention
  - tiled softmax
  - shared memory
  - online softmax
positive_tasks:
  - T_006
negative_tasks: []
saturation_tasks:
  - T_W_008
evidence:
  panel: n30_iter4
  delta: 0.00
  note: baseline saturated, keep for real verifier not judge panel
body: |
  ## Symptom
  ...
```

优点：

```text
可 review；
可 diff；
可单独验证；
可标 positive/negative/saturation；
publish 时自动生成 bridge + pattern md。
```

---

## 12.2 Curated conflict detector

避免新 pattern 抢旧 pattern 的任务。

```bash
python scripts/check_curated_conflicts.py \
  --new pattern_rl_gradient_explosion.yaml \
  --panel all_queries.yaml
```

输出：

```text
Top affected routing rows:
T_W_004: ppo_entropy_collapse_guard → rl_gradient_explosion, risk high
T_W_002: gradient_clipping → rl_gradient_explosion, desired
T_001: unchanged
```

准入：

```text
desired swaps >= 1
undesired swaps = 0
```

---

## 12.3 Probe-before-author 工具

how 报告也建议做 “probe-before-author tooling”，先测 baseline LLM 是否已饱和。

命令：

```bash
python scripts/pre_author_probe.py \
  --task T_W_007 \
  --n 10 \
  --threshold 9.7
```

输出：

```text
C_mean=10.0, saturated=true, authoring_allowed=false
```

这能避免再次发生 iter5 的错误。

---

# 十三、P3：How 策略配置化，不要让规则散在 api.py

现在 how 的关键阈值散在代码里：

```text
_SYNTH_MIN_SIMILARITY = 0.60
_SYNTH_MIN_MARGIN = 0.015
_CURATED_RESCUE_TOP_K = 5
_CURATED_PREFERENCE_MARGIN = 0.15
_CURATED_MIN_SIMILARITY = 0.60
```

建议移到：

```yaml
configs/policy_d07ddac.yaml
```

内容：

```yaml
synth_gate:
  min_similarity: 0.60
  min_margin: 0.015

curated_rescue:
  top_k: 5
  domain_lock: true
  min_similarity: null

curated_preference:
  margin: 0.15
  min_similarity: 0.60
  domain_lock: true

snippet_mode:
  S_CURATED_VERIFIED: full
  A_CURATED_REVIEWED: full
  C_MUSE_SYNTH: lightweight
  D_AUTODRAFT: lightweight

abstain:
  high_baseline_threshold: 9.7
  low_confidence_margin: 0.015
```

每次实验记录：

```text
policy_config_hash
```

这样才能复现。

---

# 十四、实施排期：6 周可落地版本

## Week 1：止血与可复现

目标：先让实验可信。

任务：

```text
1. 修 `/admin/reload` 非幂等。
2. content_hash 覆盖 routing-critical fields。
3. reload deterministic ordering。
4. routing_canary。
5. frozen bundle 机制。
6. server-PID 内 paired harness。
7. 旧 adaptive snippet tests 修复。
```

验收：

```text
连续 reload 10 次 top-K 一致。
edit/revert 后结果等于 fresh restart。
测试全绿。
任何实验都有 frozen_bundle_id。
```

---

## Week 2：Bridge Schema v2

任务：

```text
1. domain enum。
2. source_tier enum。
3. provenance fields。
4. known_negative_tasks / saturated_tasks。
5. validate_bridge_schema.py。
6. curated registry yaml 化。
```

验收：

```text
399 clusters 全部迁移成功。
14 curated 全部迁到 registry。
publish_to_how 前自动 validate。
```

---

## Week 3：Headroom & Authoring Pipeline

任务：

```text
1. pre_author_probe.py。
2. candidate_headroom.csv。
3. curated_conflict_detector。
4. T_W_002 专属 curated 试验。
5. existing 14 curated 正负迁移标注。
```

验收：

```text
所有新增 curated 必须有 headroom report。
baseline ≥9.7 的任务禁止 author。
T_W_002 若新增 pattern，必须 targeted n=30 正收益。
```

---

## Week 4：Contextual Routing v2

任务：

```text
1. how 请求支持 task_context。
2. contextual ranking formula。
3. negative_transfer_penalty。
4. saturation abstain。
5. source_tier-based snippet mode。
6. ABSTAIN telemetry。
```

验收：

```text
T_W_005 / T_W_006 不再被强注入导致负迁移。
Saturation panel injection_rate < 20%。
Home panel 不回退。
```

---

## Week 5：Evidence Trace v2

任务：

```text
1. intervention_trace schema。
2. snippet_hash / response_hash / mechanism detector。
3. hint_use_rate。
4. post_delta@1/3/5。
5. stats dashboard。
```

验收：

```text
每次注入有 trace。
每个 curated pattern 能统计 mechanism_used。
报告能回答“agent 是否真的用了 hint”。
```

---

## Week 6：Frontier-Eng Official Harness

任务：

```text
1. Hermes Frontier-Eng adapter。
2. official verifier runner。
3. real score normalizer。
4. placebo / shuffled / abstain 对照。
5. 10-task official smoke。
```

验收：

```text
10 tasks × 5 seeds × 20 iterations 跑通。
输出 Average Rank / Pairwise Win Rate / Valid Rate。
KH 效果不再只依赖 LLM judge。
```

---

# 十五、优先级清单

## P0 必须立刻做

```text
1. 修 reload 非幂等。
2. content_hash 覆盖 domain/topic/source_tier。
3. 冻结 bridge bundle。
4. 实验 harness 改成 server-PID 内 paired。
5. 修 adaptive snippet 相关 pytest。
```

## P1 立刻提升可靠性

```text
1. domain enum。
2. source_tier。
3. curated registry。
4. pre-author headroom probe。
5. negative transfer blocklist。
```

## P2 继续提升效果

```text
1. T_W_002 专属 curated。
2. wild panel 扩到 30+。
3. contextual routing v2。
4. ABSTAIN 策略。
5. evidence trace v2。
```

## P3 面向论文/Benchmark 证明

```text
1. Frontier-Eng official verifier harness。
2. Hermes agent runner。
3. 47-task paired A/B。
4. rank-based report。
5. know-how 自进化闭环。
```

---

# 十六、最终建议：后续不要再“盲目优化规则”，而要进入工程科学化阶段

你们当前已经证明了：

```text
know-how 有效；
curated pattern 有效；
how 策略能纠正错误路由；
HOME 面板显著提升。
```

但下一步继续提升，必须改变打法。

## 16.1 不建议继续做的事

```text
1. 不要继续凭直觉 augment standard_name。
2. 不要在 baseline 已经 9.7/10 的任务上写 curated。
3. 不要用 n=10 判断内容级改动。
4. 不要在 hot reload 不稳定时比较小幅路由变化。
5. 不要只看 ALL mean，不看 routing-changed subset。
6. 不要把当前 LLM judge panel 当作最终 Frontier-Eng 官方结果。
```

## 16.2 应该做的事

```text
1. 先修可复现。
2. 再强类型化 bridge。
3. 用 headroom map 决定写什么知识。
4. 用 negative-transfer panel 防止误伤。
5. 用 contextual routing 决定何时 abstain。
6. 用 evidence trace 证明 agent 真的用了知识。
7. 最后上 official Frontier-Eng verifier。
```

一句话总结：

> **rosclaw know-how 的第一阶段是“让知识能注入并产生正收益”；第二阶段应该是“让知识知道什么时候不该注入，并且只在有提升空间、有证据、有可复现收益的地方注入”。**

如果这个路线落地，know-how 不再只是“提示词增强模块”，而会成为 ROSClaw 体系里真正的 **物理 AI 工程经验控制层**：
Know 负责生产和治理经验，How 负责运行时最小干预，Hermes 负责执行优化，Frontier-Eng verifier 负责给真实反馈，Evidence Loop 负责让整个系统持续进化。
