# Know / How current flow (pre-v2)

Frozen at `rosclaw-know@55ce498`, `rosclaw-how@4639cd6` and
`rosclaw@fbf9a692` on 2026-08-06.

## Asset and query flow

```text
arXiv/GitHub README/Web snippet/legacy wiki
  -> rosclaw_know.research_sources / harvester
  -> NetworkX weaver + Muse pattern compiler
  -> bridge_index.json + code_patterns/*.md
  -> rosclaw_how.asset_loader
  -> How-owned symptom_index + code_pattern_library collections
  -> SemanticRouter/InMemoryRouter
  -> /wiki/v1/prompt/build
  -> core rosclaw.how.client.HowClient (only when ROSCLAW_HOW_URL is set)
```

This path is shallow at discovery time, snapshot/version provenance is not a
hard invariant, and JSON assets rather than SeekDB are the source of truth.

## Core local flow

```text
Runtime.initialize
  -> Memory creates a knowledge-store client
  -> the same client is passed to core KnowledgeInterface
  -> core seeds knowledge_graph
  -> the same client is passed to core HeuristicEngine
  -> MCPHub exposes local query_knowledge / task_pack / recovery tools
```

The shared client is the most important boundary defect: world knowledge,
heuristic rules and robot experience can share one database/path. V2 must
fail closed when Know and Memory resolve to the same database or directory.

## Feedback flow

```text
How injection -> injection_outcomes -> NDJSON export
  -> Know feedback_distill/evidence_distill
  -> bridge_reweighter changes priority
  -> bridge JSON publish -> How reload
```

The legacy loop accepts rich execution traces and simulation/robot events.
V2 narrows the cross-boundary payload to governance-only feedback containing
IDs, verdict, bounded reason and receipt/practice references.

## Safety flow

Hard safety policy is implemented in How/core rule modules and runtime safety
components. The knowledge service is not part of authorization. This property
must remain true: v2 How may return cognitive recommendations but cannot issue
an ActionEnvelope, Permit, Body mutation or daemon command.

## Package/version/capability freeze

| Component | Version | Observed role |
|---|---:|---|
| rosclaw-know | 1.1.1 | compiler and JSON asset publisher |
| rosclaw-how | 1.1.1 | v1 API, How policy and production SeekDB collections |
| rosclaw | 1.0.1 | runtime orchestration plus duplicated local algorithms |
| pyseekdb in How metadata | `>=1.0` | embedded/server Collection client |
| pyseekdb pinned by core dev | `1.3.0` | native storage validation target |

The Stage 0 probe executed against installed `pyseekdb 1.4.0.post1` and
`pylibseekdb 1.3.0.post4`. Embedded collection creation completed in an
isolated temporary directory. The inspected API exposes embedded and server
clients, collections, namespaces, full-text configuration, sparse vectors,
hybrid search and RRF. Native multi-vector storage is not exposed by one
collection, so v2 uses coordinated typed collections. Server-side
`AI_RERANK` was not reachable in this environment and remains a named
degradation, not an assumed capability. Server SQL DDL is supplied as
transactional migrations but cannot be marked integration-tested without a
server endpoint.
