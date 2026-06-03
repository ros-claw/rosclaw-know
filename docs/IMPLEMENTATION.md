# rosclaw-know 实施说明文档

> **范围**：本文档是 `rosclaw-know` 单仓库的深度实施参考。
> 跨仓库总览（know + how + mcp）见 `ros-claw/rosclaw-know-how-mcp/docs/IMPLEMENTATION.md`。
> 真值锚点：`docs/ROADMAP.md`（phase 完成状态）、`docs/CHANGELOG.md`（按 phase 的 commit 历史）、`data/assets/_runtime_stats.json`（实时数字快照）。

---

## 0. 摘要

`rosclaw-know` 是 ROSClaw 知识层的**离线 + 半在线**子系统，把开源机器人 / 控制 / ML 语料精炼成 *(症状 → 修复模式)* 二元组，并把这些模式以可热加载的 `bridge_index.json` 暴露给运行时姊妹项目 [`rosclaw-how`](https://github.com/ros-claw/rosclaw-how)。它**不**直接对话 agent —— agent 通过 [`rosclaw-know-how-mcp`](https://github.com/ros-claw/rosclaw-know-how-mcp) 间接调用本仓的 HTTP API（`/know/v1/research`）。

### 当前运行时数字（2026-05-20 快照，最新参见 `data/assets/_runtime_stats.json`）

| 指标 | 值 |
|---|---|
| bridge_index 簇数 | **349** |
| graph nodes / edges | 342 / 2052 |
| pattern 文件数（`code_patterns/*.md`） | 349（**342** Muse-minted + **7** curated） |
| lifecycle 分桶 | staging 23 · production 2 · demoted 3 · 历史 unbucketed 321 |
| 域分布 | Planning_Decision 190 · Learning_Training 59 · Perception_Vision 42 · Control_Locomotion 22 · Systems_Compute 19 · Memory_Reasoning 15 · World_Physics 2 |
| 测试 | 77/78 PASS（1 pre-existing pipeline mock 跳过，无关 Phase 4-10 工作） |

### 当前 master

| Commit | Phase 标签 | 摘要 |
|---|---|---|
| `23cc62a` | fix(#1) | research worker 0-source 短路 + 90s timeout cap + reload 600→120s |
| `fb6c90d` | Phase 10 [C] | 多源 extractor prompts（paper / repo / tutorial / web） |
| `31f12d1` | chore | 端口 47820 / 47821 迁移 |
| `030d78b` | Phase 9 | agent-callable HTTP API + Python SDK |

---

## 1. 角色定位

```
                          ┌──────────────────────┐
                          │   开源语料             │
                          │   - 历史 wiki (6097p) │
                          │   - awesome lists     │
                          │   - arXiv / GitHub    │
                          │   - blind-spot 自起草 │
                          └──────────┬───────────┘
                                     │ Phase 1 / 5 / 7 / 8
                                     ▼
            ┌─────────────────────────────────────────────────┐
            │           rosclaw-know  (本仓)                   │
            │  ─────────────────────────────────────────────  │
            │  pipeline: harvester → weaver → muse → publish  │
            │  feedback_distill → bridge_reweighter           │
            │  active_learning  → autodraft                   │
            │  awesome_fetcher  → ingest                      │
            │  research_worker  → on-demand mine              │
            │                                                 │
            │  产物:                                          │
            │    data/assets/bridge_index.json  ← 与 how 契约 │
            │    data/assets/code_patterns/*.md ← 模式正文    │
            │    data/assets/pattern_metrics.json (Phase 4)   │
            │    data/assets/_runtime_stats.json (快照)       │
            └────────┬───────────────────────────────┬────────┘
                     │                                │
                     │ bridge + patterns (启动读+热重)│ HTTP /know/v1/research (Phase 9)
                     ▼                                ▼
            ┌─────────────────────────────────────────────────┐
            │  rosclaw-how (47820, 运行时注入)                  │
            └─────────────────────────────────────────────────┘
```

**是什么**：
- **离线精炼器**：把 markdown 源料经过 LLM 抽取 → graph 构建 → 跨域类比 → 模式落盘，得到一份可被运行时检索的"过来人经验"库。
- **半在线调研服务**：Phase 9 在 47821 端口暴露一个 FastAPI，让 OS-level agent（openclaw / Harmes）按需为 benchmark 任务 deep-research，把新材料 mine 进库再触发 how 热重载。
- **反馈消化器**：把 how 累积的 `injection_outcomes` 蒸馏成 `pattern_metrics.json`，n-weighted 合并回 `bridge_index.json`，让路由层能 demote 表现差的模式、晋级表现好的。
- **主动学习器**：Phase 7 周期性轮询 how 的 `/wiki/v1/blind_spots`，对未覆盖的高频症状 prefix 用 DeepSeek 起草 markdown 草稿 → 自动 ingest → 让下次相似查询能命中。

**不是什么**：
- ❌ 不持有 agent 任务上下文（那是 agent 自己 + how）
- ❌ 不做 inference-time 注入（那是 how 的 state_router / semantic_router）
- ❌ 不是 LLM 包装层（only DeepSeek API wrapper inside `llm.py`，agent 不与 know 之间直接对话）

---

## 2. 完整架构

四层 + 一条反馈环：

```
┌── Source Layer ─────────────────────────────────────────────────┐
│  wiki/<page>.md         legacy ROSClaw wiki 页面（6097 个）       │
│  wiki/auto_drafted/     Phase 7 起草（autodraft.py）              │
│  wiki/awesome_corpus/   Phase 8 awesome 列表拉取                  │
│  wiki/research_corpus/  Phase 9 调研 worker 落盘                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           │ source_manifest dirty detection (Phase 5)
                           │ (SHA-256 content_hash)
                           ▼
┌── Extraction Layer ─────────────────────────────────────────────┐
│  harvester.process_single_page  · LLM 抽取 JSON                  │
│    → {symptom, fix_pattern, anti_pattern, fix_code, ...}         │
│    → 按 frontmatter.source_type 选 5 套 prompt 之一 (Phase 10[C]) │
│  ast_extract.py · Python 代码签名摘要                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌── Synthesis Layer ──────────────────────────────────────────────┐
│  weaver.build_memory_graph  · networkx DiGraph                   │
│    nodes:  {symptom, fix, code, anti_pattern}                    │
│    edges:  co-mention + 语义邻近                                  │
│                                                                  │
│  muse.compile_muse_assets  · LLM 跨域类比 + Unified-diff          │
│    cluster + 邻居域 → DeepSeek MUSE_PROMPT                       │
│    → bridge_index.symptom_clusters[cid] + pattern_<id>.md        │
│                                                                  │
│  curated_publisher · 把 7 个手写模式合并进上面两个产物             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌── Asset Layer (=与 how 契约) ──────────────────────────────────┐
│  data/assets/bridge_index.json     symptom_clusters + indexes    │
│  data/assets/code_patterns/*.md    full pattern markdown         │
│  data/assets/pattern_metrics.json  per-pattern stats (Phase 4)   │
│  data/source_manifest.json         incremental cache (Phase 5)   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 启动时读 / POST /admin/reload 热加载
                           ▼
                ┌──────────────────────────┐
                │  rosclaw-how (47820)     │
                │   SeekDB embedded ANN    │
                └────────┬─────────────────┘
                         │
                         │ outcomes export
                         │ (NDJSON / SeekDB)
                         ▼
┌── Feedback Layer ───────────────────────────────────────────────┐
│  feedback_distill.aggregate                                      │
│    outcomes → PatternMetric(n, uplift_mean, win_rate, last_seen) │
│                                                                  │
│  bridge_reweighter.reweight_bridge_index                         │
│    n-weighted merge per cluster                                  │
│    priority 转换:                                                 │
│      staging(0) + n≥5 + Δ > +0.05 → production(1)                │
│      staging(0) + n≥5 + Δ < -0.05 → demoted(-1)                  │
│      production(1) + n≥5 + 全负 → demoted(-1) (soft-deprecate)   │
│                                                                  │
│  active_learning.autodraft_for_blind_spots (Phase 7)             │
│    /blind_spots → DeepSeek 起草 → wiki/auto_drafted/             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据契约

`rosclaw-know` 与外部世界的稳定边界。下面是每个产物的字段规范。

### 3.1 `data/assets/bridge_index.json`

**how 启动时读、`/admin/reload` 热重时读**。如果格式破，how 起不来。

```json
{
  "symptom_clusters": {
    "<cluster_id>": {
      "standard_name":       "<one-sentence symptom canonical form>",
      "domain":              "<one of FRONTIER_DOMAINS>",
      "matched_keywords":    ["...", "..."],
      "cross_domain_analogies": [
        {
          "source_domain":      "<FRONTIER_DOMAINS>",
          "neighbor_id":        "<other cluster_id or pattern_id>",
          "insight":            "...",
          "action_suggestion":  "..."
        }
      ],
      "associated_patterns": ["pattern_<slug>", "curated_id", ...],
      "priority":            0,                  // optional; see below
      "is_staging":          false,              // optional; mirrors priority==0
      "safety_label":        "<SAFETY_LABELS or null>",   // curated only
      "source":              "muse" | "curated" | "autodraft" | "awesome:<list>"
                                                  // optional, ≥Phase 7
    }
  },
  "safety_label_index": {
    "<SAFETY_LABEL>": ["<curated_pattern_id>"]
  }
}
```

**`priority` 字段语义**（最容易踩坑的一处）：

| `priority` | 含义 | 来源 |
|---|---|---|
| `-1` | demoted（soft-deprecate） | Phase 4 reweight 负向；Phase 7 promote 降级 |
| `0` | staging（新生 / 未经反馈检验） | Phase 7 autodraft；Phase 8 awesome ingest；Muse 新出 |
| `1` | production | Phase 7 promote 晋级 |
| (字段缺省) | legacy production | Phase 7 之前的写法，路由按 production 处理 |

**真值字段**（Phase 4 ＋ Phase 6）：

| Field | Type | 含义 |
|---|---|---|
| `uplift_mean` | float | n-weighted 平均 post_score - pre_score 提升 |
| `uplift_n` | int | 样本数（参与 reweight 的 outcome 条数） |
| `win_rate` | float | uplift > 0.05 的比例 |

### 3.2 `data/assets/code_patterns/<id>.md`

每个 cluster 至少关联一个 .md 文件。命名规则：

| 类型 | 命名 | 例 |
|---|---|---|
| Muse-minted | `pattern_<slug>.md` | `pattern_navmorph_a_self_evolving_world_model.md` |
| Curated（手写） | `<id>.md`（无前缀） | `anti_windup_pid.md` |

**Frontmatter（YAML）**：

```yaml
---
id: <id, same as filename stem>
domain: Control_Locomotion
source_type: paper | repo | tutorial | web | curated
priority: 1            # optional; mirrors bridge cluster
safety_label: Torque_Overflow   # curated only
generated_at: 2026-05-20T17:38:18Z
---
```

**Body 章节（强制 4 段，Muse 与 curated 都遵守）**：

```markdown
## Symptom
<one-paragraph standard form of the failure pattern>

## Fix
<unified-diff style fix or step-by-step recipe>

## Anti-pattern
<what people try that makes it worse — placeholder if source didn't say>
_(no anti-pattern documented in source)_

## Cross-domain
<insights from analogous patterns in other domains>

## Patch (optional, Muse only)
```diff
--- a/before.py
+++ b/after.py
@@
-buggy_line
+fixed_line
```
```

Phase 8 加固：`muse._write_pattern_file` 强制 emit Anti-pattern 段；没找到时填占位符（永不偷偷略过 heading）。

### 3.3 `data/source_manifest.json`

Phase 5 增量缓存。**可丢，丢了下次会全量重 mine**。

```json
{
  "records": {
    "<absolute_path>": {
      "sha256":           "<content_hash>",
      "size":             12345,
      "first_seen":       "2026-05-15T10:23:00Z",
      "last_processed":   "2026-05-20T17:38:00Z",
      "extracted_id":     "<harvester-assigned id, optional>"
    }
  }
}
```

### 3.4 `data/assets/pattern_metrics.json`

Phase 4 中间产物，`bridge_reweighter` 的输入。

```json
{
  "<pattern_id>": {
    "n": 12,
    "uplift_mean": 0.18,
    "win_rate": 0.75,
    "last_seen": "2026-05-23T14:00:00Z",
    "demoted": false
  }
}
```

### 3.5 `data/assets/_runtime_stats.json`

`scripts/snapshot_stats.py` 生成的运行时快照（**git tracked**），用于回答"现在系统多大"。AI agents 与 deployer 应优先看它，不要去推算。

---

## 4. Phase 总览

| Phase | 任务 | 关键产物 |
|---|---|---|
| **1** | 离线一次性 mine 6097 个 wiki 页面 → 初始 bridge_index | `pipeline.run_phase1` |
| **2** | 与 how 联调（asset_loader 读 bridge） | (code 在 how 仓) |
| **3** | SeekDB 嵌入式 ANN 启用 | (code 在 how 仓) |
| **4** | 反馈环：outcomes → metrics → reweight | `feedback_distill`, `bridge_reweighter` |
| **5** | 增量 ingest（content-hash dirty + selective Muse） | `source_manifest`, `incremental_pipeline`, `scripts/ingest.py` |
| **6** | 可观测 + 性能（trend 分析） | `stats_analyze`, `scripts/bench_phase6.py` |
| **7** | 主动学习 + staging 晋级 | `active_learning`, `scripts/autodraft.py`, `scripts/promote.py` |
| **8** | Awesome list 外环 | `awesome_fetcher`, `scripts/ingest_awesome.py` |
| **9** | Agent-callable HTTP API | `api.py`, `research_*` |
| **10 [C]** | source-type-aware extractor prompts | `prompts.py`, `harvester.py` |

每个 phase 都有对应 verify 脚本（见 §9.2）。

---

## 5. 模块详解

模块按出现频率 + 数据流位置排序。所有路径相对 `src/rosclaw_know/`。

### 5.1 `config.py` — 全局配置 / .env

62 行。导出常量供其他模块 import。负责：

- 解析 `.env`（`dotenv.load_dotenv`）
- 暴露：`PROJECT_ROOT`, `DATA_DIR`, `ASSETS_DIR`, `CODE_PATTERNS_DIR`, `WIKI_DIR`
- DeepSeek 配置：`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_EXTRACTOR_MODEL`, `DEEPSEEK_MUSE_MODEL`
- Embeddings：`EMBEDDING_MODEL`（默认 `paraphrase-multilingual-MiniLM-L12-v2`）
- Mock 模式：`MOCK_LLM`（`ROSCLAW_KNOW_MOCK_LLM=1` 时不调真 LLM）
- `ensure_dirs()`：创建所有输出目录（幂等）
- `llm_configured()`：能否做真 LLM 调用？

> **必读 gotcha**：默认值 `DEEPSEEK_EXTRACTOR_MODEL=deepseek-v4-flash` 和 `DEEPSEEK_MUSE_MODEL=deepseek-v4-pro` 是**占位**，**不可直接生产用**。这两个是 reasoning 模型——所有 token 进 `reasoning_content`，本仓代码读 `content` 字段，会返回空。生产必须在 `.env` 里显式设 `deepseek-chat`。详见 `AGENTS.md`。

### 5.2 `llm.py` — DeepSeek 异步 wrapper

184 行。封装 OpenAI 兼容的 `/v1/chat/completions`。导出：

| API | 用途 |
|---|---|
| `chat(system, user, model, *, want_json=False, retries=3) -> str` | 通用 chat 调用，返回 `content` 字符串 |
| `chat_json(system, user, model, **kw) -> dict` | 强制要求 JSON 回包并解析 |
| `get_token_usage() -> dict` | 累计已使用的 prompt / completion token 数 |
| `reset_token_usage()` | 重置累计计数（测试用） |
| `LLMError` | 所有 LLM 调用错误的 base exception |

特性：
- 内置 retry（指数退避，3 次）
- Mock 模式：`config.MOCK_LLM` 时返回固定 `_mock_response`，让 CI 不烧钱
- Token usage tracking：方便估算每次 phase 1/research 的成本

### 5.3 `prompts.py` — Prompt 模板

219 行。所有 LLM prompt 文本字面量。导出：

| 常量 | 阶段 | 用途 |
|---|---|---|
| `FRONTIER_DOMAINS` | 1+ | 7 个 embodied-AI 域：Planning_Decision / Control_Locomotion / Learning_Training / Perception_Vision / Memory_Reasoning / Systems_Compute / World_Physics |
| `EXTRACTOR_PROMPT` | 1 | 通用 extractor（兜底） |
| `EXTRACTOR_PROMPT_PAPER` | 10[C] | 论文 / arXiv abstract 专用 |
| `EXTRACTOR_PROMPT_REPO` | 10[C] | GitHub README 专用 |
| `EXTRACTOR_PROMPT_TUTORIAL` | 10[C] | 教程 / blog 专用 |
| `EXTRACTOR_PROMPT_WEB` | 10[C] | 通用网页专用 |
| `PLANNER_PROMPT` | 1 | （备用，pipeline 的 planner 阶段） |
| `MUSE_PROMPT` | 1 | 跨域类比生成 |
| `extractor_prompt_for(source_type)` | 10[C] | 按 source_type 路由到对应 prompt；未知 → 通用 |

**Phase 10 [C] source_type 别名映射**（`_SOURCE_TYPE_PROMPTS` 字典）：

```python
"paper"          → EXTRACTOR_PROMPT_PAPER
"repo"           → EXTRACTOR_PROMPT_REPO
"tutorial"       → EXTRACTOR_PROMPT_TUTORIAL
"web"            → EXTRACTOR_PROMPT_WEB
"github_readme"  → EXTRACTOR_PROMPT_REPO       # 历史别名
"html_text"      → EXTRACTOR_PROMPT_WEB        # awesome_fetcher 写的标签
"pdf_meta"       → EXTRACTOR_PROMPT_PAPER      # PDF 几乎一定是 paper
```

### 5.4 `ast_extract.py` — Python AST 摘要

53 行。从 markdown 里 grep 出 fenced ` ```python ` 代码块或 .py 路径引用，用 `ast.parse` 提取函数签名 + docstring 首行，拼成一份压缩签名表附加到 extractor LLM 输入末尾。让 LLM 在抽 fix_pattern 时能看到 "这个 wiki 页引用了 `apply_grad_clipping(model, max_norm=1.0)`" 之类的细节。

### 5.5 `harvester.py` — Async LLM 抽取

195 行。**Phase 1 主要 LLM token 消费者**之一（另一个是 Muse）。

| API | 用途 |
|---|---|
| `process_single_page(path, session, semaphore, infra_conn)` | 单文件抽取协程 |
| `run_harvester(paths)` | 批量抽取，asyncio.gather + 信号量限并发 |
| `_parse_frontmatter(text)` | YAML 标量字段 dict（Phase 10[C] 新加） |
| `_strip_frontmatter(text)` | 砍掉 frontmatter 拿正文 |
| `_looks_useful(text)` | 滤掉太短 / 没工程内容的页面 |

**Phase 10 [C] 关键代码路径**：

```python
fm = _parse_frontmatter(raw_text)
source_type = fm.get("source_type") or fm.get("fetch_kind")
extractor_prompt = extractor_prompt_for(source_type)   # 5 套之一
result = await chat_json(session, extractor_prompt, combined, ...)
```

并发：默认 5（`config.HARVESTER_CONCURRENCY`），可通过 `.env` 覆盖。
持久化：每页抽完写 SQLite `data/rosclaw_knowledge.db`（`infra.py`）—— 资料夹被删除了下次还能 dedup。
分桶：按 `FRONTIER_DOMAINS` 一项分类，落 `domain` 字段。

### 5.6 `weaver.py` — NetworkX 构图

| API | 用途 |
|---|---|
| `load_heuristics() -> list[dict]` | 从 SQLite 读全部已抽取的启发条目 |
| `build_memory_graph(heuristics=None) -> nx.DiGraph` | 构图：node = {symptom, fix, code}，edge = 共现 + 语义近邻 |

边的类型：
- `co_mention`：同一个 page 里出现的 (symptom, fix) 对
- `same_domain`：同 `FRONTIER_DOMAINS`
- `semantic_nn`：sentence-transformers 嵌入余弦近邻（top-k 邻居）

图属于 cheap step——纯内存 networkx，全图构建 < 1s。

### 5.7 `muse.py` — 跨域类比 + Unified-diff 生成

305 行。**Phase 1 最重的 LLM 阶段**。一个 cluster 一次 LLM 调用。

| API | 用途 |
|---|---|
| `compile_muse_assets(graph, *, ...) -> dict` | 主入口；async；批量处理 cluster |
| `compile_muse_assets_sync(graph, **kw) -> dict` | 同步 wrapper（CI 友好） |
| `_generate_analogy(cluster, neighbors, session)` | 调 DeepSeek MUSE_PROMPT，返回 (analogy, patch_diff) |
| `_write_pattern_file(cid, ...)` | 落 markdown：4 段强制结构 + Anti-pattern placeholder |
| `_make_unified_diff(node_id, symptom, fix, failed)` | 生成 Patch 段 |
| `_extract_meaningful_keywords` | 给 `bridge_index` 写 `matched_keywords` 字段 |

输出：
- 写 `data/assets/bridge_index.json` `symptom_clusters` 条目
- 写 `data/assets/code_patterns/pattern_<id>.md`

**重要不变量**（Phase 8 加固）：每个生成的 .md **必有** 4 段 heading，Anti-pattern 找不到就用 `_(no anti-pattern documented in source)_` 占位。

### 5.8 `curated_patterns.py` + `curated_publisher.py` — 7 个手写模式

331 + 159 行。绝不被 Muse 覆盖。

```python
@dataclass(frozen=True)
class CuratedPattern:
    pattern_id: str
    safety_label: str          # rosclaw-how SAFETY_RULES 引用的标签
    standard_name: str
    domain: str                # FRONTIER_DOMAINS 之一
    matched_keywords: list[str]
    fix_pattern: str
    failed_attempt: str
    before_code: str
    after_code: str
    cross_domain_hints: list[dict]
```

**`CURATED_SAFETY_PATTERNS` 列表（7 个）**：

| `pattern_id` | `safety_label` | 域 |
|---|---|---|
| `anti_windup_pid` | Torque_Overflow | Control_Locomotion |
| `sliding_window_kv_cache` | Memory_Exhaustion | Memory_Reasoning |
| `gradient_clipping` | Numerical_Instability | Learning_Training |
| `output_saturation_clamp` | Torque_Overflow | Control_Locomotion |
| `closed_loop_replanning` | Velocity_Divergence | Planning_Decision |
| `exponential_backoff_retry` | Compile_Error | Systems_Compute |
| `ppo_entropy_collapse_guard` | PPO_Collapse | Learning_Training |

`curated_publisher.publish_curated_assets()` 在 `pipeline.run_phase1` 的最末步骤跑：
- 写 7 个 .md（裸 id 命名，不带 `pattern_` 前缀）
- 合并 7 条 `symptom_clusters` 进 `bridge_index.json`
- 注入 `safety_label_index` —— how 的 SAFETY 策略走这张表做精确匹配

### 5.9 `pipeline.py` — Phase 1 orchestrator

105 行。

| API | 用途 |
|---|---|
| `collect_wiki_files(...)` | 解析 `wiki/` 下的所有 .md，可子集采样 |
| `run_phase1(*, max_pages, ...)` | 完整流水线：harvester → weaver → muse → publish |

CLI 入口：`scripts/run_phase1.py`。典型调用：

```bash
.venv/bin/python scripts/run_phase1.py --max-pages 200   # 试水
.venv/bin/python scripts/run_phase1.py                   # 全量
```

成本估算（DeepSeek 价格）：
- 80-cluster mine: ~0.6 RMB
- 349-cluster 全量: ~2.5 RMB
- 时间：~10-20 min（80 cluster）/ ~60 min（349 cluster）

### 5.10 `source_manifest.py` — content-hash dirty detection

Phase 5 的核心。

| API | 用途 |
|---|---|
| `SourceRecord` | dataclass：每个文件的 sha256 / size / first_seen / last_processed |
| `SourceManifest.load(path)` | 读 `data/source_manifest.json`（缺失返回空 manifest） |
| `SourceManifest.select_dirty(paths)` | 返回 `[(path, status), ...]`，status ∈ {NEW, CHANGED, UNCHANGED} |
| `SourceManifest.mark_processed(path)` | 记录该文件为已处理 |
| `sha256_of(path)` | 文件 SHA-256（buf 8KB） |

幂等性：重跑 ingest 同一个文件 → status = UNCHANGED，skip。

### 5.11 `incremental_pipeline.py` — selective Muse

290 行。Phase 5 主入口。

| API | 用途 |
|---|---|
| `run_incremental_ingest(new_paths, *, manifest_path, dry_run)` | 完整增量：manifest → harvester (dirty only) → weaver → Muse (new clusters only) → merge |
| `merge_into_bridge(new_entries, *, bridge_path)` | non-destructive 合并：保留 Phase 4 stats |
| `_gather_candidate_paths(paths)` | 路径 → 平铺 .md 文件列表 |

**Non-destructive 合并语义**：
- 已有 cluster + 新 cluster 同 id → 合并字段，保留 `priority`, `uplift_mean`, `uplift_n`, `win_rate`, `last_seen`
- Muse 重写 `cross_domain_analogies`（这是 LLM 生成的，可以更新）
- `safety_label`, `source` 字段保留（如果之前有的话）

也就是说 Phase 4 反馈数字**不会**被 Phase 5 ingest 抹掉——这是闭环正确性的关键。

### 5.12 `feedback_distill.py` — outcomes → metrics

177 行。Phase 4 第一步。

| API | 用途 |
|---|---|
| `PatternMetric` | dataclass: n / uplift_mean / win_rate / last_seen / demoted |
| `aggregate(outcomes)` | 流式聚合：group by pattern_id |
| `distill(exports_dir, out_path)` | 从 `data/exports/outcomes-*.jsonl` 读 → 写 `data/assets/pattern_metrics.json` |
| `is_demoted(metric, *, threshold=-0.05)` | 判定阈值 |

**aggregation 规则**：
- 一行 outcome = `{pattern_id, pre_score, post_score, ts, ...}`
- `uplift = post_score - pre_score`
- `uplift_mean = sum(uplift) / n`
- `win_rate = count(uplift > 0.05) / n`
- 至少 `MIN_SAMPLE_SIZE=5` 条才考虑 demote / promote

读取来源（按优先级）：
1. `data/exports/outcomes-YYYYMMDD.jsonl`（how 的 export 端点 dump）
2. SeekDB direct read（高级用法）

### 5.13 `bridge_reweighter.py` — n-weighted merge

157 行。Phase 4 第二步。

| API | 用途 |
|---|---|
| `_load_metrics(path)` | 读 `pattern_metrics.json` |
| `_aggregate_for_cluster(cluster_id, metrics)` | 按 cluster 聚合所有关联 pattern 的 metrics（n-weighted） |
| `_every_contrib_demoted(cluster_id, metrics)` | 判定：所有 contributing pattern 都 demote？ |
| `reweight_bridge_index(bridge, metrics)` | 主入口；产出修改后的 bridge dict |

**n-weighted 公式**（per-cluster aggregation）：

```
n_total       = Σ n_pi for pi in cluster.associated_patterns
uplift_mean   = Σ (n_pi * uplift_mean_pi) / n_total
win_rate      = Σ (n_pi * win_rate_pi)    / n_total
```

**优先级转换规则**：

```python
if priority == 0 and n_total >= 5 and uplift_mean > +0.05:
    priority = 1     # staging → production
elif priority == 0 and n_total >= 5 and uplift_mean < -0.05:
    priority = -1    # staging → demoted
elif priority in (1, None) and n_total >= 5 and _every_contrib_demoted(cluster_id, metrics):
    priority = -1    # production → demoted (soft-deprecate)
```

幂等：重跑同样的 outcomes 数据 → 同样的 priority 转换。

### 5.14 `active_learning.py` — 主动学习

235 行。Phase 7 核心。

| API | 用途 |
|---|---|
| `fetch_blind_spots(url)` | 同步 HTTP，调用 how 的 `/wiki/v1/blind_spots` |
| `autodraft_for_blind_spots(*, n=5, dry_run=False)` | 主入口：拉盲点 → DeepSeek 起草 → 落 `wiki/auto_drafted/` |
| `_draft_one(blind_spot, session)` | 单条草稿 |
| `_write_draft(prefix_hash, body, *, out_dir)` | 落盘，命名 `auto_<hash>.md` |
| `_build_user_prompt(blind_spot)` | 把 blind_spot 转成 LLM prompt |

**Draft frontmatter**：

```yaml
---
id: auto_<8-char-hash>
domain: <inferred from prefix>
source_type: tutorial   # autodraft 都是 step-by-step 风格
priority: 0             # MUST be staging
generated_at: <iso>
source: autodraft
---
```

CLI：`scripts/autodraft.py --then-ingest` —— 起草完直接跑 `scripts/ingest.py`，端到端。

### 5.15 `awesome_fetcher.py` — Phase 8

387 行。把外部 awesome list（如 `hslatman/awesome-industrial-control-system-security`、`A-make/awesome-control-theory`）批量 fetch + 入库。

| API | 用途 |
|---|---|
| `AwesomeEntry` / `FetchResult` | dataclass |
| `parse_readme(markdown)` | 解析 markdown bullet 风格的 awesome 条目 |
| `_parse_html_anchors(html, ...)` | 解析 HTML-table 风格（hslatman/* 这种） |
| `fetch_awesome_readme(repo_url)` | 拉 README（GitHub raw API） |
| `fetch_one(entry, ...)` | 抓单条目（按 URL 类型分流：repo / html / pdf） |
| `fetch_awesome_list(url, *, section, limit)` | 主入口 |

**双格式 parser 的必要性**：
- A-make/awesome-control-theory：markdown bullet 列表（`- [Name](url) — description`）
- hslatman/awesome-industrial-control-system-security：HTML `<table>`（项目 / 描述 / 链接 / 标签）
- 两个 parser 自动 fallback，没匹配上的格式抛 warning 跳过

**典型流量**：
- 47 个 entries → 16 个新 staging cluster（转换率 34%，典型 landing-page 重）
- 23/23 staging cluster 4/4 完整度
- 端到端 verify：`verify_phase8_awesome.py` 2/2 PASS（PID + dead time sim 0.82；ICS PLC unauth cmd sim 0.54）

### 5.16 `seekdb_align.py` — bridge → SeekDB schema

| API | 用途 |
|---|---|
| `_get_client()` | 拿 seekdb embedded handle |
| `_get_embed_model()` | sentence-transformers 单例 |
| `check_duplicate_and_align(...)` | 判一个待入库 cluster 是否与已有 cluster 太像（cosine ≥ threshold） |

**关键阈值**：`similarity_threshold = 0.88`（**不是** 0.85 的笔误，实测 0.85 会过度合并）。这是离线 cluster 合并阈值，**不同于** how 侧 `similarity_floor = 0.5`（运行时 CATALYST 召回拒绝阈值）。详见 §14 gotcha。

### 5.17 `stats_analyze.py` — Phase 6 趋势分析

约 190 行。把 how 的 `/stats` 多份快照做时序回归。

| API | 用途 |
|---|---|
| `PatternTrend` | dataclass: slope / samples / classification |
| `fetch_stats(url)` | 调 how `/wiki/v1/stats` |
| `snapshot_stats(...)` | 写一份带时间戳的快照 → `data/stats_history/` |
| `load_history(history_dir)` | 读全部快照 |
| `analyze_trends(snapshots)` | 对每个 pattern 跑 linear regression → trend |
| `render_markdown_report(trends)` | 输出 markdown 报告 |
| `run(*, snapshot_only=False)` | CLI 入口 |

分类规则（`_classify`）：
- `slope > +0.01 / day` AND `samples >= 5` → "improving"
- `slope < -0.01 / day` AND `samples >= 5` → "degrading"
- otherwise → "stable" / "insufficient_data"

CLI：`scripts/analyze_stats.py`。

### 5.18 Phase 9 HTTP API stack

四个模块共同实现 `/know/v1/research/*` 端点：

#### `research_store.py` — append-only job store

```python
@dataclass(frozen=True)
class ResearchJob:
    job_id: str
    topic: str
    depth: str             # "shallow" | "deep"
    budget_tokens: int
    status: str            # "queued" | "running" | "completed" | "failed"
    created_at: str
    updated_at: str
    sources_planned: int = 0
    sources_fetched: int = 0
    clusters_added: int = 0
    error: str | None = None
    summary: str | None = None
    new_cluster_ids: list[str] = ...
```

`ResearchStore` 是 JSONL append + 内存 dict + threading.Lock 的最简实现。

#### `research_sources.py` — 多源 fetcher

270 行。三个 source channel（每个独立超时 10s）：
- arXiv（`http://export.arxiv.org/api/query`）
- GitHub（API + raw README fallback；可选 `GITHUB_TOKEN`）
- Brave Web Search（可选 `BRAVE_SEARCH_API_KEY`，无 key 跳过）

入口：`collect_sources(topic, depth)`，asyncio.gather 三路并行。

#### `research_worker.py` — 串行 worker

192 行。**Phase 10 [know#1] 修复后的版本**：

- `_run_loop`：单 worker 队列，catch all Exception → status=failed
- `_run_job`：5 步流水
  1. `collect_sources` 包 `asyncio.wait_for(timeout=_COLLECT_TIMEOUT=90s)` 防 DNS hang
  2. **0-source short-circuit**：三路全空 → 立刻 status=completed clusters_added=0
  3. 落盘 `wiki/research_corpus/<job_id>/<filename>.md`
  4. `run_incremental_ingest([out_dir])` → 复用 Phase 5
  5. `_notify_how_reload`：POST `/wiki/v1/admin/reload`（timeout=120s）

> **fix(know#1) 关键事实**：之前 reload 默认 timeout=600s，agent 跑空 topic 时看似"hang forever"实际是等 reload。现在三道防御：90s collect cap + 0-source 短路 + 120s reload cap。

#### `api.py` — FastAPI endpoints

189 行。

| Method | Path | 用途 | Auth |
|---|---|---|---|
| POST | `/know/v1/research` | 启动一个调研 job → 返回 `job_id` | optional X-API-Key |
| GET | `/know/v1/research/{job_id}` | 轮询单个 job 状态 | same |
| GET | `/know/v1/research?limit=20` | 列最近 N 个 job | same |
| GET | `/healthz` | 探活 | no |

调用 stack：MCP `rosclaw_research` → SDK `KnowClient.research()` → 这里。

CLI 启动：`scripts/run_research_server.py`（默认端口 `47821`，可 `ROSCLAW_KNOW_PORT` 覆盖）。

### 5.19 `infra.py` — SQLite 缓存

93 行。`data/rosclaw_knowledge.db` 的简单 wrapper。两张表：
- `extracted_heuristics(page_path, page_hash, ts, payload_json)`：harvester 输出去重
- `mined_clusters(cluster_id, status, payload_json)`：Muse 输出去重

**Safe to delete**：丢了下次会重新抽取（成本 = 一次全量 harvester run）。

---

## 6. 脚本一览

`scripts/` 下 19 个 CLI 入口：

| 脚本 | 阶段 | 用途 |
|---|---|---|
| `run_phase1.py` | 1 | 完整 mine（`--max-pages` 控制规模） |
| `inspect_samples.py` | 1 | 随机抽 N 个 cluster 检查 4 段完整度 |
| `verify_frontier_eng.py` | 1 | 模式产出格式校验 |
| `ingest.py` | 5 | 增量 ingest 单文件 / 目录 |
| `lint_bridge.py` | 5 | bridge_index 体检（orphan / missing / dup / stale-demotion） |
| `verify_phase5_ingest.py` | 5 | 端到端：新文件 → reload → 路由命中（< 1 s） |
| `bench_phase6.py` | 6 | SLO baseline（build / feedback / reload / export 的 p95） |
| `analyze_stats.py` | 6 | 跑 `stats_analyze.run` 输出 markdown 报告 |
| `snapshot_stats.py` | 6 | 写 `data/assets/_runtime_stats.json`（手动版） |
| `distill_feedback.py` | 4 | 跑 feedback_distill |
| `reweight_bridge.py` | 4 | 跑 bridge_reweighter |
| `replay_benchmark.py` | 4 | 60-rollout 合成 A/B：6/6 模式分类正确 |
| `autodraft.py` | 7 | 主动学习入口（可附 `--then-ingest`） |
| `promote.py` | 7 | 跑 staging 晋级 / 降级（`--apply` 才落地） |
| `verify_phase7_active.py` | 7 | 8 步端到端联调 |
| `ingest_awesome.py` | 8 | awesome list 入口（`--section`, `--limit`, `--then-ingest`） |
| `verify_phase8_awesome.py` | 8 | awesome → ingest → reload → CATALYST 验证 |
| `verify_phase9_agent.py` | 9 | 4 端点端到端 |
| `run_research_server.py` | 9 | 启动 FastAPI（默认 47821） |
| `publish_to_how.py` | * | 手动触发 how `/admin/reload` |

---

## 7. HTTP API

Phase 9 三个端点 + healthz。详见 `api.py`。

### `POST /know/v1/research`

**Request**:
```json
{
  "topic": "PID quadrotor anti-windup",
  "depth": "shallow",        // optional, "shallow" | "deep"
  "budget_tokens": 50000     // optional, 1000..500000
}
```

**Response**（201 Created）:
```json
{
  "job_id": "j_8f3a...",
  "topic": "PID quadrotor anti-windup",
  "depth": "shallow",
  "budget_tokens": 50000,
  "status": "queued",
  "created_at": "2026-06-02T10:23:00Z",
  "updated_at": "2026-06-02T10:23:00Z"
}
```

### `GET /know/v1/research/{job_id}`

**Response**：同上的 ResearchJob 全字段（含 `sources_planned`, `sources_fetched`, `clusters_added`, `summary`, `new_cluster_ids`）。

Poll 间隔建议 ≥ 10 s（worker 是单任务串行）。

### `GET /know/v1/research?limit=20`

最近 N 个 job，按 `created_at` desc。

### `GET /healthz`

```json
{
  "status": "ok",
  "queue_size": 0,
  "active_job_id": null
}
```

---

## 8. 关键算法 / 工程技巧

### 8.1 `priority` 字段的三态语义

见 §3.1。这是路由 + 学习闭环的核心，所有 phase 都依赖它：
- Phase 5 ingest：新 cluster 默认 `priority=0`
- Phase 7 autodraft：起草的 priority=0
- Phase 8 awesome：入库的 priority=0
- Phase 4 reweight：按 n 和 uplift_mean 触发转换

如果你引入新模块，**新 cluster 也应该走 staging 默认**（priority=0），等真实 feedback 决定晋级。

### 8.2 Content-hash delta detection

Phase 5。`source_manifest.SourceManifest.select_dirty` 三态：

```python
NEW       = manifest 里没这个 path
CHANGED   = path 存在但 sha256 不同
UNCHANGED = sha256 一致 → skip
```

收益：86 个 wiki 页面已 mined，加 1 个新页面 → harvester 只跑 1 次而非 87 次。Phase 6 把这个技巧延伸到 SeekDB upsert（"no-change reload 442ms"）。

### 8.3 n-weighted reweight

`bridge_reweighter._aggregate_for_cluster`：

```
关联 N 个 pattern，每个有 (n_i, uplift_i, win_i)
cluster 的"真"指标 = 加权平均，权重 = n_i
```

不直接平均的原因：某个 pattern 跑过 100 次 uplift=0.2，另一个 pattern 跑过 1 次 uplift=-0.8 —— 直接平均会被噪声拖偏。

### 8.4 Staging 晋级门

`scripts/promote.py` 的核心：

```
if n_total >= MIN_SAMPLE_SIZE and priority == 0:
    if uplift_mean > +PROMOTION_DELTA: promote_to(+1)
    if uplift_mean < -PROMOTION_DELTA: demote_to(-1)
```

`MIN_SAMPLE_SIZE = 5`、`PROMOTION_DELTA = 0.05`（可调）。`--apply` 加上才真改 bridge_index，默认 dry-run。

### 8.5 Awesome list 双格式 parser

`awesome_fetcher.parse_readme` 优先用 markdown 解析；失败时降级到 `_parse_html_anchors` 解析 HTML `<table>`。两个 parser 独立，互不污染（同一篇 README 不会被两次解析）。

### 8.6 Phase 10 [C] source-type 路由

`prompts.extractor_prompt_for(source_type)` 在 `harvester.process_single_page` 调用前选 prompt。frontmatter 里 `source_type` 字段（或别名 `fetch_kind`）决定路由：

- 论文：强调"hypothesis / experimental setup / ablation"
- repo：强调"problem / typical failure mode / API contract"
- tutorial：强调"step / mistake students make / correct fix"
- web：通用兜底
- 缺省：通用 `EXTRACTOR_PROMPT`（向后兼容历史语料）

效果：抽取召回率显著上升（论文不再把 "experimental result" 误抽成 fix_pattern）。

### 8.7 Research worker 防 hang 三道防御（know#1 修复）

详见 §5.18：

```
collect_sources  ─── asyncio.wait_for(timeout=90s) ─── hang 防御
                     ↓
                  0 sources?
                  ┌─yes ─→ status=completed clusters_added=0  (短路)
                  └─no  ─→ pipeline + reload
                              ↓
                          urlopen timeout=120s  (而不是 600s)
```

---

## 9. 测试与验证

### 9.1 单元测试（pytest）

`tests/` 下 10 个 `test_*.py`，77 / 78 PASS（1 pre-existing pipeline mock 跳过）。运行：

```bash
.venv/bin/python -m pytest -q                # 全部
.venv/bin/python -m pytest -q tests/test_research_worker.py -v   # 单文件
```

| 文件 | 覆盖 |
|---|---|
| `test_active_learning.py` | Phase 7 起草 / fetch_blind_spots / draft frontmatter |
| `test_awesome_fetcher.py` | markdown + HTML-table 双格式 parser |
| `test_bridge_reweighter.py` | n-weighted 聚合 + 三态转换 |
| `test_feedback_distill.py` | aggregate + write_metrics + is_demoted |
| `test_incremental_pipeline.py` | dirty detection + non-destructive merge |
| `test_lint_bridge.py` | orphan / missing / dup / stale-demotion 检测 |
| `test_pipeline.py` | end-to-end mock LLM |
| `test_source_manifest.py` | SourceManifest 9 case |
| `test_stats_analyze.py` | linear regression + classification |
| `test_research_worker.py` | **know#1 修复**：0-source 短路 + collect timeout marks failed |

CI 友好：`ROSCLAW_KNOW_MOCK_LLM=1` 跑全套不花 LLM 钱。

### 9.2 端到端验证（verify_*.py）

每个 phase 都有一支：

| Verify | Phase | 步骤 |
|---|---|---|
| `verify_frontier_eng.py` | 1 | 模式格式校验（4 段 + frontmatter） |
| `verify_phase5_ingest.py` | 5 | 新文件 → manifest 标 dirty → harvester → bridge merge → how /admin/reload → CATALYST 在 < 1 s 内命中 |
| `verify_phase7_active.py` | 7 | 8 步：blind_spot → autodraft → ingest → reload → CATALYST → 5× 正反馈 → promote → 终态 is_staging falsy（6/6 PASS） |
| `verify_phase8_awesome.py` | 8 | awesome fetch → ingest → reload → 命中新 staging cluster（2/2 PASS：PID 0.82，ICS 0.54） |
| `verify_phase9_agent.py` | 9 | mcp SDK 4 端点联调（init / search / build / feedback，4/4 PASS） |

**前置条件**：所有 verify_* 假设 how 在 `127.0.0.1:47820` 运行；如果是单仓 dev，先 `ln -s ../rosclaw-know/data/assets data/assets` 软链。

---

## 10. 部署 + 配置

### 10.1 `.env` 变量

| 变量 | 必需 | 默认 | 用途 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅（生产） | — | `sk-...` 前缀 |
| `DEEPSEEK_BASE_URL` | ❌ | `https://api.deepseek.com` | 私有部署可改 |
| `DEEPSEEK_EXTRACTOR_MODEL` | ❌ | `deepseek-v4-flash`（占位） | **生产改成 `deepseek-chat`** |
| `DEEPSEEK_MUSE_MODEL` | ❌ | `deepseek-v4-pro`（占位） | **生产改成 `deepseek-chat`** |
| `ROSCLAW_KNOW_MOCK_LLM` | ❌ | `0` | `1` = 不调真 LLM（CI 用） |
| `ROSCLAW_KNOW_PORT` | ❌ | `47821` | Phase 9 FastAPI 端口 |
| `WIKI_DIR` | ❌ | `wiki` | 历史 wiki 源料（相对 PROJECT_ROOT） |
| `HARVESTER_CONCURRENCY` | ❌ | `5` | asyncio semaphore 上限 |
| `EMBEDDING_MODEL` | ❌ | `paraphrase-multilingual-MiniLM-L12-v2` | **必须与 how 一致** |
| `GITHUB_TOKEN` | ❌（Phase 9） | — | 提升 GitHub search rate limit |
| `BRAVE_SEARCH_API_KEY` | ❌（Phase 9） | — | 不设就跳过 web 通道 |
| `ROSCLAW_HOW_RELOAD_URL` | ❌ | `http://127.0.0.1:47820/wiki/v1/admin/reload` | 调研完成后自动 reload how |
| `ROSCLAW_HOW_API_KEY` | ❌ | `rw_sk_dev_local` | 调用 how `/admin/reload` 用 |

### 10.2 第一次安装

```bash
git clone https://github.com/ros-claw/rosclaw-know.git
cd rosclaw-know
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"   # dev 含 pytest
cp .env.example .env
# 编辑 .env：至少设 DEEPSEEK_API_KEY 和 *_MODEL = deepseek-chat
```

### 10.3 第一次 mine

```bash
# (1) 准备 wiki 源料 —— 用 symlink 把你的语料指过来
ln -s /path/to/rosclaw-wiki wiki

# (2) 试水（200 页，~10 min，~0.6 RMB）
.venv/bin/python scripts/run_phase1.py --max-pages 200

# (3) 抽 30 个 cluster 检查格式
.venv/bin/python scripts/inspect_samples.py --n 30

# (4) ≥85% 合格率 → 全量
.venv/bin/python scripts/run_phase1.py

# (5) 写一份 runtime 快照
.venv/bin/python scripts/snapshot_stats.py

# (6) bridge 体检
.venv/bin/python scripts/lint_bridge.py
```

### 10.4 启动 HTTP API（Phase 9，给 mcp + agent 调用）

```bash
# 后台跑
nohup .venv/bin/python scripts/run_research_server.py > /tmp/know.log 2>&1 &

# 探活
curl -fsS http://127.0.0.1:47821/healthz
# → {"status":"ok","queue_size":0,...}

# 启 how（在 ../rosclaw-how/）后，触发一次手动 reload 验证联通
curl -X POST http://127.0.0.1:47820/wiki/v1/admin/reload \
     -H "X-API-Key: $ROSCLAW_HOW_API_KEY" -d '{}'
```

---

## 11. 监控与运维

### 11.1 周期任务（建议 crontab）

```cron
# 每天凌晨 4 点写一份 stats snapshot（Phase 6）
0 4 * * *   cd /opt/rosclaw-know && .venv/bin/python scripts/snapshot_stats.py

# 每周一蒸馏反馈 + 反馈 reweight（Phase 4）
0 5 * * 1   cd /opt/rosclaw-know && .venv/bin/python scripts/distill_feedback.py && .venv/bin/python scripts/reweight_bridge.py && .venv/bin/python scripts/publish_to_how.py

# 每周三跑 promote dry-run + 人工审 + 周五 apply
0 9 * * 3   cd /opt/rosclaw-know && .venv/bin/python scripts/promote.py > /tmp/promote_dry.log
```

### 11.2 bridge 体检

`scripts/lint_bridge.py` 报告：
- **orphan**：bridge 里有的 cluster，但 `associated_patterns` 指向的 .md 文件不存在
- **missing**：.md 文件存在但 bridge 里没引用
- **dup**：两个 cluster 的 `standard_name` 太像（cosine > 0.95）—— 提示需要合并
- **stale-demotion**：priority=-1 但 last_seen 超过 N 天，建议清掉

CI 上跑：lint 报错 → 阻止 merge bridge_index。

### 11.3 学习闭环节奏

```
agents 注入 → how /feedback → /outcomes/export → 
  distill_feedback (write metrics) → 
    bridge_reweighter (merge) → 
      publish_to_how (/admin/reload) → 
        下一轮路由用新 priority
```

每周一次足够 —— more 频繁会把短期噪声当信号。

### 11.4 promote 节奏

staging cluster (priority=0) 平均需要 3-5 周累积到 n ≥ 5。`promote.py --apply` 前必看 dry-run 报告，确认没把还没"长大"的 cluster 错误降级。

---

## 12. 故障排查

| 症状 | 可能根因 | 排查 |
|---|---|---|
| harvester 0 提取 | DEEPSEEK_*_MODEL 用了 reasoning 模型 | `.env` 改 `deepseek-chat`；重跑 |
| Muse 跑了但 .md 缺 Anti-pattern | 历史代码版本 < `0934eb0` | 升级到 Phase 8 hardening 版本 |
| `verify_phase5_ingest.py` 失败 | how 没起 / 端口不对 / API key 错 | `curl how/healthz` 看 |
| research worker hang | （已修）reload timeout 600s | 升级到 `23cc62a` 之后版本 |
| awesome ingest 转化率为 0 | URL 是 HTML-table 但 markdown parser 没匹配 | 看 logs，确认 `_parse_html_anchors` 被调 |
| autodraft 写了 0 个 file | how `/blind_spots` 空 OR DeepSeek 返回非法 JSON | 看 `data/exports/` 有没有 outcomes |
| 增量 ingest 反复重 mine 同一个文件 | manifest 被 wipe | 检查 `data/source_manifest.json` 是否存在 |

---

## 13. 性能与成本

### 13.1 单次操作

| 操作 | 时间 | 成本 |
|---|---|---|
| harvester 单页 | 2-5 s | ~500 tokens |
| Muse 单 cluster | 8-15 s | ~3-5 k tokens |
| weaver build_memory_graph | < 1 s | 0 |
| 增量 ingest 单页 | 5-10 s | 500-5000 tokens |
| autodraft 单 blind-spot | 10-20 s | ~3 k tokens |
| awesome fetch 单 entry | 2-5 s 网络 | 0 |
| research_worker shallow job | 60-180 s | 10-30 k tokens |
| research_worker deep job | 180-600 s | 30-80 k tokens |

### 13.2 批量

| 任务 | 时间 | 成本 |
|---|---|---|
| Phase 1 全量（349 cluster） | ~60 min | ~2.5 RMB |
| 47-task benchmark warmup | 2.5-5 h | ~$4-5 USD |
| 一次 promote dry-run | < 1 s | 0 |
| 每日 snapshot_stats | < 1 s | 0 |

---

## 14. 已知 gotchas

### 14.1 DeepSeek 模型坑

`deepseek-v4-flash` / `deepseek-v4-pro` 是 reasoning 模型。它们的 token 进 `reasoning_content`，公开的 `content` 字段是**空字符串**。harvester / muse 读 `content`，所以静默返回 0 抽取。生产必须 `deepseek-chat`。

### 14.2 两个 0.x 阈值不同

| 名字 | 默认 | 在哪 | 用途 |
|---|---|---|---|
| `similarity_threshold` | **0.88** | `seekdb_align.py` | 离线 cluster 合并 |
| `similarity_floor` | **0.5** | `rosclaw-how/config.py` `SIMILARITY_FLOOR` | 在线 CATALYST 拒绝 |

混了这两个会把整个路由 / 入库逻辑搞反。

### 14.3 `priority` 缺省 = legacy production

Phase 7 之前没有 staging 概念，老 cluster 都没 priority 字段。**how 把"缺省 priority"按 production 处理**（priority==1 视同）。这意味着：
- 老 cluster 不会被自动降到 staging
- 老 cluster 永远参与路由（除非显式 demote）

如果你想清理历史 cluster，要么显式 `priority: -1` 老老实实 demote，要么用 `scripts/lint_bridge.py --stale-days 60` 找 last_seen 超过 60 天的候选。

### 14.4 `phase1_status.md` 是历史快照

`docs/phase1_status.md` 是 2026-05-16 冻结快照（"80 clusters / 240 analogies"），早就过时。**不要拿这个文件做现状判断**，看 `_runtime_stats.json` 或本文档 §0。

### 14.5 prefix 不一致是正常的

- `cluster_<id>` —— weaver 出来的、Muse 写过 markdown 的 cluster id
- `pattern_<slug>.md` —— Muse 生成的模式文件名
- 裸 id（`anti_windup_pid` 等）—— curated 模式（手写，永不被 Muse 覆盖）

`associated_patterns` 字段里这两类混排是 by-design。

### 14.6 Awesome list 默认 priority=0（staging）

Phase 8 ingest 的 cluster 全部入 staging。**不要**期望 awesome 入库的内容立刻被路由优先采用——等真实 agent 反馈才晋级（n ≥ 5 + uplift > 0.05）。

### 14.7 `data/source_manifest.json` 可以删

但删了 = 下次全量 mine。建议定期 archive 一份，万一手抖 `git clean -fxd` 还能复原。

---

## 15. 扩展点

### 15.1 加新 source channel（论文/repo/web 以外）

例子：把 Notion / Confluence 内部文档拉进来。

1. 在 `research_sources.py` 加一个 `_notion_search(topic) -> list[FetchedSource]`
2. `collect_sources` 里加进 `asyncio.gather`
3. 文档明示 `NOTION_API_KEY` env 变量
4. 用 `source_type="repo"` 或新加 `"notion"` 别名到 `_SOURCE_TYPE_PROMPTS`

### 15.2 加新 domain

1. `prompts.FRONTIER_DOMAINS` 加新条目（例如 `"Communication_Protocol"`）
2. 更新 `EXTRACTOR_PROMPT*` 里的 domain 列表
3. 跑全量 Phase 1 重 mine（旧 cluster 的 domain 字段不会自动变）—— 或者写一个迁移脚本把新 domain 标到合适的现有 cluster

### 15.3 加新 source_type extractor prompt

1. `prompts.py` 加 `EXTRACTOR_PROMPT_<NEW>` 字面量
2. `_SOURCE_TYPE_PROMPTS` 映射加入
3. `harvester.py` 不需要改（已经走 `extractor_prompt_for(source_type)`）
4. 写 frontmatter 时把 `source_type: <new>` 标对

### 15.4 加新 verify 脚本

新 phase 闭环 ≥ 3 步时考虑加一支 `verify_phaseN_*.py`：
- 起一个干净状态（mock how / 临时 sqlite）
- 跑端到端
- 关键 assertion 计数（"3/3 PASS"）
- 输出 markdown 风格的结果方便贴 PR

### 15.5 调 `MIN_SAMPLE_SIZE` 和 `PROMOTION_DELTA`

在 `bridge_reweighter.py` 顶部。调小 = 更激进晋级（短期偏差 → noisy）；调大 = 更保守（错过短窗口模式）。当前 `MIN_SAMPLE_SIZE=5 / PROMOTION_DELTA=0.05` 是 60-rollout 实测最稳的。

---

## 16. 与 how / mcp 的契约

### 16.1 与 how 的边界

| know 写 | how 读 |
|---|---|
| `data/assets/bridge_index.json` | 启动 + `/admin/reload` |
| `data/assets/code_patterns/*.md` | 启动 + `/admin/reload` |
| `data/assets/pattern_metrics.json` | （不读，仅给 reweight 用） |

| how 写 | know 读 |
|---|---|
| `data/exports/outcomes-YYYYMMDD.jsonl` | `feedback_distill.distill(exports_dir=...)` |
| `/wiki/v1/blind_spots` HTTP | `active_learning.fetch_blind_spots()` |
| `/wiki/v1/stats` HTTP | `stats_analyze.fetch_stats()` |

### 16.2 与 mcp 的边界

mcp 不直接 read / write know 的文件。所有 know → mcp → agent 走 HTTP：

| MCP tool | 后端调用 |
|---|---|
| `rosclaw_research` | POST `/know/v1/research` |
| `rosclaw_research_status` | GET `/know/v1/research/{id}` |

详见 mcp 仓 `src/rosclaw_know_how_mcp/client.py` 的 `KnowClient`。

### 16.3 部署相互依赖

- how **不能独立工作**——它需要 know 产物（bridge_index + code_patterns）才能路由
- know **可以独立工作**——可以单独跑 mine / autodraft / awesome ingest，不需要 how 启动；只有 `_notify_how_reload` 会失败（best-effort）
- mcp **依赖两者都起来**——`rosclaw_research` 直接走 know，`rosclaw_*` 其他 7 个都走 how

---

## 17. ROADMAP / 下一步

完整 ROADMAP 见 `docs/ROADMAP.md`，本节摘 know 视角的下一步：

| 候选 | 触发 | 工作量 |
|---|---|---|
| **N1. Benchmark agent 跑 Frontier-Eng** | 用户已声明的主线，等结果回来 | 0（系统就绪） |
| **N2. 真实 outcomes 落回 Phase 4** | benchmark 跑完→ `distill_feedback` →`reweight_bridge` | 2-3 天 |
| **N3. 域分布纠偏** | Planning_Decision 占 190/349 太重，World_Physics 只 2 | 1 周 awesome ingest |
| **N4. research_worker 多 worker** | 若 47 task 串行太慢，考虑加并行（注意 how `/admin/reload` 不带内部锁，需要小心） | 1-2 天 |
| **N5. 内存压力调研** | swap 已 6.1G / 8G，profile know 进程 RSS | 半天 |
| **N6. promote 历史持久化** | 现在 promote.py 无状态，加 `promote_history.jsonl` | 1 天 |

`N1` 是主线，benchmark 跑出真实数据前 N2-N6 都属"先别动"。

---

## 附录 A — `bridge_index.json` 字段全表

```jsonc
{
  "symptom_clusters": {
    "<cluster_id>": {
      // 必填
      "standard_name":          "string",
      "domain":                 "FRONTIER_DOMAINS",
      "matched_keywords":       ["string"],
      "cross_domain_analogies": [
        {
          "source_domain":     "FRONTIER_DOMAINS",
          "neighbor_id":       "string",
          "insight":           "string",
          "action_suggestion": "string"
        }
      ],
      "associated_patterns":    ["string"],          // pattern md filename stems

      // optional / 半静态
      "priority":               -1 | 0 | 1 | null,   // null = legacy production
      "is_staging":             true | false,        // 镜像 priority==0
      "safety_label":           "SAFETY_LABELS|null", // curated only
      "source":                 "muse|curated|autodraft|awesome:<list>",

      // optional / Phase 4 反馈
      "uplift_mean":            -1.0..+1.0,
      "uplift_n":               int,
      "win_rate":               0.0..1.0,

      // optional / 调试
      "last_seen":              "ISO 8601"
    }
  },
  "safety_label_index": {
    "Torque_Overflow":          ["anti_windup_pid", "output_saturation_clamp"],
    "Memory_Exhaustion":        ["sliding_window_kv_cache"],
    "Numerical_Instability":    ["gradient_clipping"],
    "Velocity_Divergence":      ["closed_loop_replanning"],
    "Compile_Error":            ["exponential_backoff_retry"],
    "PPO_Collapse":             ["ppo_entropy_collapse_guard"]
  }
}
```

## 附录 B — pattern `.md` frontmatter

```yaml
---
id: pattern_<slug> | curated_id
domain: Control_Locomotion | Learning_Training | Perception_Vision |
        Planning_Decision | Memory_Reasoning | Systems_Compute | World_Physics
source_type: paper | repo | tutorial | web | curated | autodraft
priority: -1 | 0 | 1                # mirrors bridge cluster
safety_label: <SAFETY_LABELS>       # optional, curated only
generated_at: ISO 8601
source: <repo URL / arXiv id / awesome:<list> / curated>   # optional
---
```

## 附录 C — 7 个 curated 模式速查

| ID | safety_label | 域 | 一句话 |
|---|---|---|---|
| `anti_windup_pid` | Torque_Overflow | Control_Locomotion | 饱和时停积分，按 back-pressure 思路 |
| `output_saturation_clamp` | Torque_Overflow | Control_Locomotion | `torch.clamp(tau, ±tau_max)` 在控制环出口 |
| `sliding_window_kv_cache` | Memory_Exhaustion | Memory_Reasoning | KV 缓存只保留滑窗内最近 N 个 token |
| `gradient_clipping` | Numerical_Instability | Learning_Training | `clip_grad_norm_(model.parameters(), max_norm)` 防 NaN |
| `closed_loop_replanning` | Velocity_Divergence | Planning_Decision | 状态偏离 → 立即重规划，不死跑残值 |
| `exponential_backoff_retry` | Compile_Error | Systems_Compute | 失败重试间隔 `base * 2^attempt` + jitter |
| `ppo_entropy_collapse_guard` | PPO_Collapse | Learning_Training | 监控 policy entropy，跌穿阈值时回退 lr / 增 entropy bonus |

---

> 本文档由 rosclaw-know 实例撰写。
> 真值源：`docs/ROADMAP.md` / `docs/CHANGELOG.md` / `data/assets/_runtime_stats.json` / 源代码。
> 跨仓库总览请参见 `ros-claw/rosclaw-know-how-mcp/docs/IMPLEMENTATION.md`。
