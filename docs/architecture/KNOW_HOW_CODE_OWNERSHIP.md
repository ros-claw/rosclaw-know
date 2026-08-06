# Know / How v2 code ownership audit

Date: 2026-08-06
Audited commits: `rosclaw-know@55ce498`, `rosclaw-how@4639cd6`,
`rosclaw@fbf9a692`.

This is the Stage 0 freeze record. It describes the code that exists before
the v2 migration; it is not a claim that the target ownership has already
been reached.

## Ownership rules

- `rosclaw-know` owns external-world acquisition, immutable snapshots,
  source-linked wiki compilation, knowledge-unit indexing, retrieval and
  Reference Packs.
- `rosclaw-how` owns task/runtime context interpretation and advisory output.
- `rosclaw` owns optional loading, wire protocols, orchestration, agent/MCP
  exposure and adapters to Body, Sandbox, Practice, Memory and EventBus.
- Memory/Practice evidence is never copied into the Know database. How is
  advisory and has no Action/Permit/daemon authority.

## rosclaw-know

| File | Current responsibility | Duplicate today | Future authority / migration | Compatibility / deletion |
|---|---|---:|---|---|
| `src/rosclaw_know/api.py` | v1 research and TaskPack HTTP API | Partly, core adapters expose catalog operations | Know v2 HTTP authority; retain v1 routes as adapters | Keep v1 through v2 compatibility window |
| `research_sources.py` | shallow arXiv/GitHub README/Web discovery | No equivalent deep source layer | Split into planner plus typed source adapters; discovery only | Keep wrapper until v1 research callers migrate |
| `research_store.py` | JSONL research-job store | No | Back jobs with canonical Store protocol | Retain import adapter; JSONL ceases to be truth |
| `research_worker.py` | fetch Markdown, run legacy pipeline, reload How | Core has no bounded research worker | Replace internals with v2 research orchestrator | Keep v1 job shape adapter |
| `awesome_fetcher.py` | awesome-list candidate fetch | No | Candidate discovery adapter only | Retain |
| `harvester.py`, `prompts.py`, `llm.py`, `ast_extract.py` | LLM symptom/fix extraction | Core has hard-coded equivalent facts | Legacy compiler behind importer; v2 evidence-linked compilers become authoritative | Deprecate only after deterministic export parity |
| `pipeline.py`, `incremental_pipeline.py`, `source_manifest.py` | JSON/Markdown compilation and hash delta | Core `know/assets_loader.py`, `batch_engine.py` duplicate orchestration | Reuse hashing/incremental ideas under v2 snapshots/wiki units | Legacy entrypoints retained behind flag |
| `weaver.py`, `graph_builder_v2.py` | NetworkX graphs | Core `know/graph.py` and `storage.py` duplicate knowledge graph | Relations stored canonically in SeekDB | NetworkX becomes compiler analysis only |
| `muse.py`, `pattern_compiler_v2.py`, `curated_*` | pattern synthesis and publishing | Core `KnowledgeInterface` embeds the same safety patterns | Know remains sole catalog compiler | Core embedded catalog is deprecated after adapter parity |
| `schemas.py`, `bridge_schema.py` | v1.5 typed catalog contracts | Core uses unversioned dicts | Extended by strict v2 wire contracts | Existing schemas retained for legacy assets |
| `hybrid_retriever.py` | deterministic in-process weighted ranker | How `SemanticRouter` and core keyword matcher are competing rankers | Replaced by canonical v2 hybrid retriever backed by Know Store | Export adapter preserves TaskPack ordering where possible |
| `seekdb_align.py` | optional duplicate detection only | How owns production SeekDB collections | Superseded by v2 Know store and capability probe | Remove only after v2 migration completes |
| `task_pack_builder.py`, `asset_loader.py` | preflight TaskPack construction | Core `task_pack_adapter.py` calls it; no algorithm copy | Legacy projection over Reference Pack | Keep v1 contract adapter |
| `evidence_writer.py`, `evidence_distill.py`, `feedback_distill.py`, `bridge_reweighter.py` | execution/uplift evidence and ranking feedback | Practice/Memory own raw experience | Only governance feedback remains in Know; raw traces stay Practice/Memory | Raw-trace ingest APIs are legacy and must not feed v2 Know |
| `sim_ingest/**`, `extractors/trajectory_extractor.py` | robot/sim trajectories converted to knowledge | Directly conflicts with v2 boundary | Freeze as legacy experiment path; v2 accepts only source evidence or governance feedback | Candidate for later removal, never used by v2 store |
| `taskcard/**`, `extractors/benchmark_extractor.py` | TaskCard compiler | Core also has `know/task_card.py` | Know owns external/project cards; core owns runtime context projection | Preserve TaskCard v1 adapter |
| `scripts/*bridge*`, `scripts/compile_pattern_cards.py`, `scripts/build_task_pack.py` | legacy asset lifecycle | Core has asset loading only | Import/export tooling around v2 store | Keep until bridge export is no longer required |

## rosclaw-how

| File | Current responsibility | Duplicate today | Future authority / migration | Compatibility / deletion |
|---|---|---:|---|---|
| `src/rosclaw_how/api.py` | `/wiki/v1`, health and intervention API | Core `HowClient` and local `HeuristicEngine` provide two runtime paths | Add `/how/v2/*`; v1 delegates into v2 pipeline | Keep v1 routes |
| `seekdb_client.py`, `asset_loader.py` | canonical runtime collections built from Know JSON assets | This is misplaced Know storage/indexing | Move knowledge storage/retrieval authority to `rosclaw-know`; How keeps only Know clients | Delete after service/in-process migration and one compatibility release |
| `semantic_router.py`, `inmemory_router.py` | dense routing and fallback | Know hybrid ranker and core keyword matcher | Replace with a Know Protocol client; no separate v2 ranker | Retain only for `/wiki/v1` rollback flag |
| `error_normalizer.py`, `state_router.py`, `score_normalizer.py`, `topic_group.py` | runtime failure/state classification | Core intervention copy overlaps | How remains authority; core becomes adapter | Retain |
| `snippet_composer.py`, `intervention_policy.py`, `runtime_diagnoser.py`, `safety_router.py` | v1.5 advice policy | Byte/near-byte copies under core `how/intervention/` | Standalone How stays authority; core imports optionally via Protocol | Core copies deprecated, not immediately deleted |
| `intervention/schemas.py`, `intervention/decision_engine.py`, `intervention/*` | structured recovery intervention, including Memory inputs | Core has matching models/logic | Refactor into v2 context/advice/citation pipeline with explicit evidence origins | v1 intervention adapter retained |
| `outcomes.py`, `outcome_wal.py`, `outcome_models.py` | injection feedback | Know has feedback distillation | Map into bounded `KnowledgeUsageFeedbackV1`; no raw prompt/trajectory transfer | Retain WAL compatibility |
| `blind_spots.py` | unknown-error recurrence | Know active-learning polls it | Emit bounded KnowledgeGapRequest; no automatic truth promotion | Retain |

## rosclaw core

| File | Current responsibility | Duplicate today | Future authority / migration | Compatibility / deletion |
|---|---|---:|---|---|
| `src/rosclaw/core/runtime.py` | creates a shared Memory/Knowledge/How store and chooses local/service How | Yes; violates required database isolation | Own service manager, feature gates and separate context adapters | Keep public `Runtime.knowledge` / `.how` facades |
| `src/rosclaw/know/interface.py` | full keyword matcher, curated catalog, capability/task knowledge and Practice-aware TaskCard compilation | Duplicates both independent packages and crosses Practice boundary | Thin runtime facade over Knowledge Protocol; runtime/body facts remain local | Legacy implementation behind rollback flag, then deprecate |
| `know/storage.py`, `know/graph.py`, `know/evidence_ingest.py` | writes facts into the Memory knowledge store | Duplicates Know store and mixes world/experience data | No v2 world-knowledge writes; keep only legacy compatibility | Deprecate for v2 paths |
| `know/task_pack_adapter.py`, `know/assets_loader.py`, `know/batch_engine.py` | optional independent-package adapter and EventBus orchestration | Minimal duplication except local asset assumptions | Convert to v2 thin clients/events | Keep adapters |
| `know/task_card.py`, `embodiment_card.py`, `verifier_card.py`, `integration.py`, `g1_goalforge.py` | runtime-local knowledge models/workflows | Partial overlap with Know TaskCards | Runtime context projection only; source knowledge goes to Know | Review individually after v2 adoption |
| `how/client.py` | local HTTP How v1 client | Correct layer but v1-only | Extend as versioned v2 client | Keep v1 fallback |
| `how/engine.py`, `rules.py`, `rule_efficacy.py`, `recovery.py`, `recovery_loop.py`, `retry_orchestrator.py` | local decision/recovery algorithms | Duplicate independent How | Compatibility facade/orchestration only | Deprecate algorithms gradually |
| `how/intervention/**` | copied standalone How policy | Direct duplicate | Replace imports with optional `rosclaw_how` adapter | Keep rollback copy for one compatibility window |
| `how/selective/**`, `how/choreography/**` | core-specific runtime orchestration | No independent equivalent | Core remains authority for orchestration, never knowledge retrieval | Keep |
| `agent_runtime/mcp_hub.py` | old knowledge/TaskPack/recovery tools | Not duplicated, but tool contracts are v1 | Register read-only/advisory v2 tools through service manager | Preserve old names |
| `connectors/ros/know/ros_knowledge_seed.py` | writes ROS capability triples to shared graph | Confuses runtime state with world knowledge | Body/context adapter only; never write to Know store | Deprecate writer |
| `sense/adapters/how_context.py` | sense-to-How runtime context | No | Feed typed runtime context without persistence in Know | Keep |
| relevant tests under `tests/test_know*`, `tests/test_knowledge*`, `tests/test_how*`, `tests/how/**`, `tests/integration/test_know_how_smoke.py` | mostly local core algorithm coverage | Many do not install/call both independent packages | Add wire-contract/service/in-process/system tests; relabel local-only tests legacy | Keep as regression coverage |

## Audit answers

1. The duplicated algorithms are symptom matching/ranking, curated safety
   patterns, intervention policy, local knowledge graph storage and TaskCard
   construction.
2. Runtime currently constructs core `KnowledgeInterface`; How is a remote
   `HowClient` only when `how_url` is set, otherwise core `HeuristicEngine`.
   Standalone How itself builds SeekDB collections from Know JSON assets.
3. Most core tests exercise core-local algorithms and mocks. Only
   `test_know_runtime.py` imports `rosclaw_know`; service integration tests
   exercise standalone How v1. None covers a v2 ReferencePack wire contract.
4. Public compatibility APIs are `/know/v1/research`, `/know/v1/task-pack/build`,
   `/wiki/v1/*`, core `KnowledgeInterface`, `HowClient`, `rosclaw_task_pack`,
   `rosclaw_match_symptom`, `query_knowledge` and `get_recovery_strategy`.
5. Legacy paths are bridge/code-pattern assets, TaskPack v1, `/wiki/v1`, the
   core local Knowledge/How algorithms and the shared `knowledge_graph` store.
6. Core service manager, clients, Protocols, context/event/feedback adapters,
   old public import modules and old MCP names remain thin adapters.
7. After compatibility expiry, How's asset/SeekDB/ranker implementation,
   core algorithm copies and raw trajectory-to-Know ingest are deletion
   candidates. No deletion occurs in Stage 0.

## Baseline test freeze

- `rosclaw-know`: 504 passed, 1 failed after excluding seven test modules
  whose collection is hard-coded to `/root/workspace/...`; the one remaining
  failure is a CLI test whose script writes to the same hard-coded root.
- `rosclaw-how`: editable installation initially fails because
  `pyproject.toml` force-includes the absent `data/assets` directory.
- `rosclaw`: baseline is recorded after the isolated dev environment is
  available; current related tests primarily validate local implementations.

These are pre-existing baseline defects, not v2 regressions.
