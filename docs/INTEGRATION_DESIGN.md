# ROSClaw-Know v1.5 → rosclaw main repo: integration design

> **Status:** Phase 0-4 **shipped** 2026-06-03.
> Original design preserved below.  See § Execution log for what
> actually landed.
>
> **Date:** 2026-06-03
> **Subject:** how `rosclaw-know@1.5.0a1` joins `rosclaw@1.0.0`
> **Prior art:** `rosclaw/docs/release/v1.0/audits/audit-know.md`
> (the 2026-05-28 audit that anticipated this merge but predated
> v1.5 Sprints 5–13).

---

## Execution log

| Phase | Estimate | Actual | What landed |
|---|---|---|---|
| 0 — pre-merge sanity | 4 h | done | `pyproject.toml` slimmed (compiler deps → `[compiler]` extra); rosclaw venv installs `rosclaw-know@1.5.0a1` with only `pyyaml` added; all runtime-facing imports succeed |
| 1 — vendored bridge | 8 h | done | `rosclaw/src/rosclaw/know/{batch_engine,task_pack_adapter,assets_loader}.py` written; Runtime wires the three under `enable_knowledge=True`; 13 v1.5 assets + 349 code_patterns copied to `rosclaw/data/knowledge_assets/`; runtime smoke test loads 349 symptoms + 357 patterns vs. baseline 7 |
| 2 — runtime integration tests | 12 h | done | `rosclaw/tests/test_know_v15_integration.py` (15 tests, all green); pre-existing `test_knowledge_integration.py` + `test_know_how_runtime_e2e.py` updated to isolate the curated-baseline contract via `assets_path` injection / `monkeypatch.chdir` |
| 3 — PyPI cutover prep | 4 h | done | version cut `1.5.0.dev13 → 1.5.0a1`; `python -m build` produces ~180 KB wheel + ~220 KB sdist; install-and-import verified in a fresh venv; `docs/PYPI_PUBLICATION.md` documents the upload runbook (deferred pending PyPI policy decision) |
| 4 — asset CI workflows | 6 h | done | `rosclaw-know/.github/workflows/{ci,release-assets}.yml` (lint+test + tag-driven release with wheel+sdist+asset bundle); `rosclaw/.github/workflows/fetch-assets.yml` (manual or scheduled refresh that opens a PR) |
| **Total** | 34 h | **5 h actual** | The dev13 sprints had built all the surface area — wiring it took less than expected |

Acceptance criteria (§9 below): all green except PyPI publication
itself (gated on private/public index decision).

---

## 1.  Where we are today

### 1.1.  Two repos, two roles

| Repo | Role | Top-level package | Version |
|---|---|---|---|
| `rosclaw_wiki/rosclaw-know` | **Compiler** — turns corpus + execution feedback into the catalog | `rosclaw_know.*` | `1.5.0.dev13` |
| `rosclaw/rosclaw` | **Runtime** — embodied-AI OS, agent runtime, MCP, sandbox, etc. | `rosclaw.*` | `1.0.0` |

The 2026-05-28 audit (`audit-know.md`) called this exact split — Know
as a **batch + query hybrid module**, NOT a service.  v1.5 sharpens
that picture: the *compiler* side now has 12 sprints of machinery
(typed catalog, hybrid retrieval, evidence loop v2, sim ingest, real-
robot self-improvement, in-memory bridge_reweighter).  None of it
needs to be resident inside `rosclaw/`.

### 1.2.  What `rosclaw.know` already does today

The runtime already has `src/rosclaw/know/`:

```
rosclaw/src/rosclaw/know/
├── __init__.py          → exports KnowledgeInterface
├── interface.py         → query-side: symptom match, capability lookup,
│                          analogy retrieval (~1000 LOC, hard-coded
│                          fallback patterns + bridge_index.json reader)
├── integration.py       → KnowIntegration wrapper for Runtime
├── graph.py             → SeekDB knowledge_graph CRUD
└── storage.py           → seed_knowledge_graph (curated capability + symptom rows)
```

And the runtime spawns it via:

```python
# rosclaw/src/rosclaw/core/runtime.py (already exists)
if self.config.enable_knowledge:
    from rosclaw.know.interface import KnowledgeInterface
    from rosclaw.know.storage import seed_knowledge_graph
    self._knowledge = KnowledgeInterface(
        robot_id=self.config.robot_id,
        event_bus=self.event_bus,
        seekdb_client=seekdb,
    )
```

`KnowledgeInterface._load_bridge_index` reads
`assets_path / "bridge_index.json"` (default
`assets_path = "data/knowledge_assets"`) — the slot is already there
for the compiler's output.

### 1.3.  What v1.5 added that the audit didn't anticipate

| Feature | Where it lives | Why the runtime needs it |
|---|---|---|
| Typed catalog (Sprints 1–4) | `rosclaw_know.schemas` + `data/assets/*.yaml` | Same patterns, but typed; opens the hybrid retriever |
| Physical knowledge graph (Sprint 5) | `physical_graph.json` (142 nodes, 383 edges) | Richer than `bridge_index.json` — has TaskCards, EmbodimentCards, VerifierCards |
| Hybrid retriever (Sprint 5) | `rosclaw_know.hybrid_retriever` | BM25 + embedding + graph walk; same query API |
| Evidence Loop v2 (Sprint 6) | `rosclaw_know.evidence_distill` + `bridge_reweighter` | Placebo-adjusted uplift; promotes / demotes patterns |
| Task Pack API (Sprint 7) | `rosclaw_know.task_pack_builder` + `rosclaw_know.api` | Pre-flight knowledge for agents — exactly what runtime wants pre-`capability_invoke` |
| Sim ingest (Sprint 9) | `rosclaw_know.sim_ingest` (rosbag / Foxglove / Isaac / MuJoCo) | Same RobotEvent envelope the runtime already emits |
| Auto-derived cross-embodiment (Sprint 10) | `rosclaw_know.sim_ingest.cross_embodiment` | No hand-curated table; structural join |
| Self-improvement loop (Sprint 11) | `rosclaw_know.sim_ingest.robot_trajectory_extractor` | Promotes patterns from real-robot traces; discovers candidates |
| **bridge_reweighter direct path (Sprint 12)** | `rosclaw_know.bridge_reweighter.reweight_bridge_index_from_traces` + `sim_ingest.reweight_bridge_from_robot_events` | **One-liner**: end-of-episode → catalog updated, in-memory |
| Catalog expansion (Sprint 13) | `failure_taxonomy.yaml` + `physical_graph.json` | 8/8 canonical event_types covered |

---

## 2.  Integration architecture

The 2026-05-28 audit's two-sided diagram still holds.  v1.5 just
fills in more of it:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            ROSClaw Runtime                              │
│                                                                         │
│  ┌─────────────┐  ┌────────────┐  ┌────────────────────────────────┐   │
│  │  Firewall   │  │   Memory   │  │  Knowledge  (rosclaw.know)     │   │
│  │ (resident)  │  │ (resident) │  │  ─ query side, resident         │   │
│  └─────────────┘  └────────────┘  │  ─ batch side, EventBus-triggered│   │
│                                   └────────────────────────────────┘   │
│                                                  │                      │
│                          ┌───────────────────────┴───────────────────┐  │
│                          │ EventBus topics                            │  │
│                          │  • rosclaw.sandbox.episode.completed       │  │
│                          │  • rosclaw.runtime.execution.completed     │  │
│                          │  • rosclaw.knowledge.assets_refreshed      │  │
│                          └────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                          │                              │
                          │ query                        │ batch (EventBus)
                          ▼                              ▼
            ┌───────────────────────┐    ┌──────────────────────────────┐
            │  rosclaw.know         │    │  rosclaw_know (PyPI dep)     │
            │  (in-repo, resident)  │    │  (vendored or pip-installed) │
            │                       │    │                              │
            │  • KnowledgeInterface │◄───│  • bridge_reweighter         │
            │  • bridge_index.json  │    │  • sim_ingest.bridge_direct  │
            │  • _SAFETY_PATTERNS   │    │  • task_pack_builder         │
            │  • SeekDB triples     │    │  • hybrid_retriever          │
            └───────────────────────┘    │  • schemas (typed objects)   │
                                         └──────────────────────────────┘
                          ▲
                          │ asset publication
                          │ (build-time or cron)
            ┌─────────────┴─────────────────────────────────────────────┐
            │  rosclaw-know catalog assets                              │
            │  (built in rosclaw_wiki/rosclaw-know, copied to runtime)  │
            │                                                            │
            │  • bridge_index.json     ← query-side reads at startup    │
            │  • physical_graph.json   ← graph queries / Task Packs     │
            │  • pattern_metrics.json  ← Evidence Loop v2 priorities    │
            │  • failure_taxonomy.yaml ← FailureMode catalog            │
            │  • code_patterns/*.md    ← per-pattern action templates   │
            └────────────────────────────────────────────────────────────┘
```

---

## 3.  Merge plan (file-by-file)

### 3.1.  Three options for `rosclaw_know` itself

| Option | Pros | Cons | Recommendation |
|---|---|---|---|
| **A. PyPI dependency** — `rosclaw_know` as a pinned dep in `rosclaw/pyproject.toml` | Clean boundary; both repos stay independent; v1.5 dev velocity preserved | Requires PyPI publication of `rosclaw-know`; CI needs to handle pre-release versions | **Preferred long-term.** |
| **B. Git submodule** — `rosclaw/external/rosclaw-know/` | No PyPI publication needed; pin by SHA | Awkward for users who pip-install; `external/` paths in imports | Bridge solution; use during v1.5 dev. |
| **C. Vendor (copy) into `rosclaw/src/rosclaw/know/v15/`** | One repo, one install; users see one package | Duplicate code; drift risk; v1.5 dev becomes upstream/downstream | **Don't.**  This loses everything the audit warned against. |

**Path picked here:** **A**, with **B** as the bridge.  See §5 for the
phased rollout.

### 3.2.  What lands inside `rosclaw/src/rosclaw/know/` (new files)

The existing four files (`__init__.py`, `interface.py`, `integration.py`,
`graph.py`, `storage.py`) stay.  Add:

```
rosclaw/src/rosclaw/know/
├── batch_engine.py      ← NEW: wraps rosclaw_know for EventBus triggers
├── task_pack_adapter.py ← NEW: rosclaw_know.task_pack_builder.build() → runtime
├── assets_loader.py     ← NEW: keep bridge_index.json fresh from rosclaw_know publish
└── (existing files unchanged)
```

#### 3.2.1.  `batch_engine.py` (new)

```python
"""KnowledgeBatchEngine — runtime-side wrapper around rosclaw_know.

Listens to EventBus topics that warrant a catalog update and calls
the in-memory rosclaw_know.bridge_reweighter direct path.

EventBus contract (from audit-know.md §2.3):
  SUBSCRIBES TO
    rosclaw.runtime.execution.completed  → ingest one episode's traces
    rosclaw.sandbox.episode.completed    → same, after sim rollout
    rosclaw.knowledge.ingest_request     → manual batch trigger
  PUBLISHES
    rosclaw.knowledge.assets_refreshed   → after bridge_index update
    rosclaw.knowledge.ingest_progress    → status during batch
"""
from rosclaw.core.lifecycle import LifecycleMixin
from rosclaw.core.event_bus import Event, EventPriority

# v1.5 dep — guarded import (Option A above)
try:
    from rosclaw_know.sim_ingest import reweight_bridge_from_robot_events
    from rosclaw_know.sim_ingest.event_schema import RobotEvent
    _V15_AVAILABLE = True
except ImportError:
    _V15_AVAILABLE = False


class KnowledgeBatchEngine(LifecycleMixin):
    def __init__(self, runtime, assets_path):
        super().__init__()
        self.runtime = runtime
        self.bridge_path = assets_path / "bridge_index.json"
        self.metrics_path = assets_path / "pattern_metrics.json"

    def _do_start(self):
        if not _V15_AVAILABLE:
            return  # log a one-time warning; batch side is a no-op
        bus = self.runtime.event_bus
        bus.subscribe("rosclaw.runtime.execution.completed", self._on_episode)
        bus.subscribe("rosclaw.sandbox.episode.completed", self._on_episode)
        bus.subscribe("rosclaw.knowledge.ingest_request", self._on_ingest_request)

    def _on_episode(self, event):
        events = _episode_to_robot_events(event.payload)  # adapter
        if not events:
            return
        summary, coverage = reweight_bridge_from_robot_events(
            events,
            bridge_path=self.bridge_path,
            metrics_path=self.metrics_path,
        )
        self.runtime.event_bus.publish(Event(
            topic="rosclaw.knowledge.assets_refreshed",
            payload={"summary": summary, "coverage_violations": coverage.violations},
            source="knowledge.batch_engine",
            priority=EventPriority.NORMAL,
        ))
        # Tell the query side to reload bridge_index.json from disk:
        if self.runtime._knowledge is not None:
            self.runtime._knowledge._load_bridge_index(self.bridge_path)
```

The Sprint 12 one-liner is the whole batch side.  No multi-week
batch pipeline needed.

#### 3.2.2.  `task_pack_adapter.py` (new)

Sprint 7's Task Pack API is the runtime's pre-flight knowledge.  Wrap it:

```python
"""TaskPackAdapter — runtime-facing call to rosclaw_know.task_pack_builder."""
try:
    from rosclaw_know.task_pack_builder import build as build_task_pack
    _V15_AVAILABLE = True
except ImportError:
    _V15_AVAILABLE = False


def task_pack_for(task_id: str, *, embodiment_id: str | None = None) -> dict:
    """Return the pre-flight knowledge pack the agent should see."""
    if not _V15_AVAILABLE:
        return {"failure_modes": [], "fix_patterns": [], "warnings": []}
    return build_task_pack(task_id=task_id, embodiment_id=embodiment_id).as_dict()
```

Then `Runtime.capability_invoke` calls `task_pack_for(...)` before
selecting a provider.  MCP exposes this via a new tool below.

#### 3.2.3.  `assets_loader.py` (new)

The runtime needs to know when a fresh asset bundle is available.
This is the *publish side* the audit called out: a tiny watcher /
explicit reload trigger.  ~30 LOC; on `rosclaw.knowledge.assets_refreshed`,
call `KnowledgeInterface._load_bridge_index` to pick up the new
contents in-place.

### 3.3.  `rosclaw_know` does NOT need to move

Everything in `rosclaw_wiki/rosclaw-know/` stays where it is.  Two
things change in its build:

1. Publish to PyPI (or a private index) — at least the alpha series
   `1.5.0a1`, `1.5.0a2`, … so `rosclaw/pyproject.toml` can pin.
2. After a successful build, `cp data/assets/*.json
   data/assets/*.yaml` into `rosclaw/data/knowledge_assets/`.  Today
   that's a manual copy; in CI it becomes a release artifact.

### 3.4.  MCP tool surface

`rosclaw/src/rosclaw/mcp/` currently registers 11 tools, none for
knowledge.  v1.5 adds two natural candidates:

```
rosclaw_task_pack(task_id, embodiment_id=None) -> dict
  → calls rosclaw.know.task_pack_adapter.task_pack_for(...)

rosclaw_match_symptom(error_signature) -> dict
  → already exists as KnowledgeInterface.match_symptom; just expose it
```

Both go in `rosclaw/src/rosclaw/mcp/knowledge_tools.py`.

---

## 4.  Asset publication contract

| File | Source (in rosclaw-know) | Destination (in rosclaw) | Consumer |
|---|---|---|---|
| `bridge_index.json` | `data/assets/bridge_index.json` | `data/knowledge_assets/bridge_index.json` | `KnowledgeInterface._load_bridge_index` |
| `pattern_metrics.json` | `data/assets/pattern_metrics.json` | `data/knowledge_assets/pattern_metrics.json` | `KnowledgeBatchEngine` (priority deltas) |
| `physical_graph.json` | `data/assets/physical_graph.json` | `data/knowledge_assets/physical_graph.json` | hybrid retriever (when Option A is in) |
| `failure_taxonomy.yaml` | `data/assets/failure_taxonomy.yaml` | `data/knowledge_assets/failure_taxonomy.yaml` | future: graph drift detection on runtime startup |
| `code_patterns/*.md` | `data/assets/code_patterns/` | `data/knowledge_assets/code_patterns/` | hint composer (uses fix_summary, anti_patterns) |

CI on `rosclaw-know` adds a `release-assets` job that uploads these
to a release.  CI on `rosclaw` adds a `fetch-assets` step that
downloads the latest matching release.  No manual `cp`.

---

## 5.  Phased rollout (when greenlit)

### Phase 0  — pre-merge sanity (≈ 4 hours)
- [ ] Smoke test: `pip install -e rosclaw_wiki/rosclaw-know` inside
      `rosclaw/.venv` works
- [ ] `python -c "from rosclaw_know.sim_ingest import reweight_bridge_from_robot_events"` succeeds inside the rosclaw venv
- [ ] Confirm `data/knowledge_assets/` dir convention is honored
- [ ] Audit the two `requirements`/`pyproject.toml` files for transitive conflicts (pydantic, networkx, sentence-transformers)

### Phase 1  — vendored bridge (≈ 8 hours)
- [ ] Add `rosclaw-know` as a Git submodule under `rosclaw/external/`
- [ ] `rosclaw/pyproject.toml` adds `rosclaw-know @ file://external/rosclaw-know` (PEP 508 local-file URL)
- [ ] Build the three new modules: `batch_engine.py`, `task_pack_adapter.py`, `assets_loader.py`
- [ ] Wire `KnowledgeBatchEngine` into `Runtime.__init__` under `enable_knowledge`
- [ ] Copy current `data/assets/{bridge_index,pattern_metrics,physical_graph}.json` to `rosclaw/data/knowledge_assets/`

### Phase 2  — runtime integration tests (≈ 12 hours)
- [ ] Test: `Runtime` with `enable_knowledge=True` initializes batch engine
- [ ] Test: synthetic `rosclaw.sandbox.episode.completed` event triggers `reweight_bridge_from_robot_events`
- [ ] Test: `match_symptom("PID windup")` returns a v1.5 cluster (was a v1.0 curated pattern before)
- [ ] Test: `task_pack_for("task_robotics_pid_tuning")` returns non-empty
- [ ] Test: MCP `rosclaw_task_pack` round-trip via stdio transport

### Phase 3  — PyPI cutover (≈ 4 hours, after rosclaw-know v1.5.0 lands)
- [ ] Publish `rosclaw-know` `1.5.0a1` to PyPI
- [ ] Switch `rosclaw/pyproject.toml` from submodule URL to pinned PyPI version
- [ ] Remove the submodule; remove the `external/` path
- [ ] Re-run Phase 2 tests
- [ ] CI: install `rosclaw[all]` from a fresh venv must pull `rosclaw-know` from PyPI

### Phase 4  — asset CI (≈ 6 hours)
- [ ] `rosclaw-know` CI adds `release-assets.yml` — on tag, upload `data/assets/*.json` + `data/assets/code_patterns/` as a release artifact
- [ ] `rosclaw` CI adds `fetch-assets.yml` — on merge to main, download latest matching release into `data/knowledge_assets/`
- [ ] Remove manual asset copy from the README

**Total:** ~34 hours.  The audit's original estimate was 80 hours
because Sprint 5-12 hadn't been done yet — that work is now done.

---

## 6.  Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| **pydantic version mismatch** — `rosclaw_know` requires pydantic 2; `rosclaw` may pin to 1.x via a transitive | Medium | Audit step in Phase 0 catches it; `rosclaw` has been on pydantic-friendly code already (`how/v15/schemas.py` uses pydantic v2 idioms) |
| **Asset versioning drift** — query side reads `bridge_index.json` written by an incompatible compiler | Low | `BridgeIndexV2` schema validated at load; on mismatch, fall back to baseline patterns (existing code does this) |
| **Catalog growth makes startup slow** — 142 nodes today, could be 5000 | Low | `KnowledgeInterface.initialize` already lazy-loads; HNSW (config in CLAUDE.md) is the long-term answer |
| **Sandbox episode events at high rate** — debounce needed | Medium | `KnowledgeBatchEngine` batches incoming events on a 1s window (~ 5 LOC) |
| **v1.5 sim_ingest depends on extras (rosbag2_py, foxglove_msgs)** — heavy on the runtime image | Medium | Adapter imports already guard with `try/except ImportError`; runtime ships without rosbag deps; users opt into them |
| **Two `know/` namespaces confuse newcomers** — `rosclaw_know` vs `rosclaw.know` | Low | Document in `rosclaw/AGENTS.md`: rosclaw.know is the *interface*, rosclaw_know is the *compiler* |

---

## 7.  What does NOT change

- `rosclaw_know.*` (the compiler) keeps its own pyproject, ruff config, test suite, sprint cadence
- `rosclaw.know.interface.KnowledgeInterface` keeps its hard-coded
  `_SAFETY_PATTERNS` fallback — these are the "always-on baseline"
  even when no v1.5 assets are loaded
- `rosclaw.how.v15` (the intervention controller) is untouched —
  different "v1.5", different module, no conflict
- The 7 curated rosclaw safety patterns (Torque_Overflow,
  Velocity_Divergence, …) keep working byte-for-byte

---

## 8.  Open questions (for the user when this work starts)

1. **PyPI publication** — do we publish `rosclaw-know` to public PyPI
   or a private index?  (Both repos must remain PRIVATE on
   `github.com/ros-claw`; PyPI is a separate decision.)
2. **Asset path convention** — keep `data/knowledge_assets/` or
   rename to `data/knowledge/` to align with `data/practice/` etc.?
3. **MCP tool naming** — `rosclaw_task_pack` matches the v1.5 plan;
   confirm the prefix convention against the existing 11 tools.
4. **Versioning policy** — when does `rosclaw-know@1.5.0` get cut?
   Today we're on `1.5.0.dev13`; the alpha series alone could carry
   us through the integration.
5. **Migration of existing `data/knowledge_assets/`** — if any
   already exists in `rosclaw/`, we need to confirm we're replacing
   compatible assets, not stomping on hand-curated ones.

---

## 9.  Acceptance criteria (when integration is done)

- [ ] `rosclaw` runtime starts with `enable_knowledge=True` and
      successfully loads v1.5 `bridge_index.json` (priority counts
      visible in startup log)
- [ ] An MCP client calling `rosclaw_task_pack("task_robotics_pid_tuning")`
      gets `{ failure_modes: [failure_pid_integrator_windup, …],
      fix_patterns: [compiled_zero_integral_gain_on_saturation, …] }`
- [ ] After a sandbox episode emits a `RobotEvent` stream, the next
      task's task pack reflects the updated priorities (Sprint 6 +
      Sprint 12 loop closed end-to-end)
- [ ] `pytest rosclaw/tests/` passes (full suite)
- [ ] `pytest rosclaw_wiki/rosclaw-know/tests/` passes (full suite,
      unchanged)
- [ ] No new dependency conflicts in `pip check`
- [ ] Repos remain PRIVATE on `ros-claw` org
