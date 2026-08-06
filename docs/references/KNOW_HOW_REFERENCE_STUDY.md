# Know / How v2 reference study

The exact revisions are in `KNOW_HOW_REFERENCE_LOCK.yaml`. All repositories
were read-only shallow clones. No dependency installation or code execution
was performed, and no source code was copied into ROSClaw.

## Conclusions used by this implementation

### Repository understanding

CodeWiki first inventories a repository, derives language-specific component
and relationship data, then clusters only when the token budget requires it.
Its documentation generator processes leaf modules before parents and checks
for missing output pages. ROSClaw adopts those invariants: bounded inventory,
task-relevant structural analysis, leaf-first synthesis and an evidence guard.
It does not adopt CodeWiki's `eval` parsing or its generic clone execution
path.

The RepoMaster URL in the source plan returned `Repository not found` on
2026-08-06 and GitHub search did not locate a verifiable moved repository.
The lock deliberately records this rather than silently substituting another
project.

### Research planning and citations

STORM separates perspective/question generation, retrieval, outline creation
and cited synthesis. Its knowledge-base objects retain a citation mapping.
ROSClaw reuses the separation and bounded multi-perspective plan, but every
final project/wiki/unit claim must point to an immutable SourceSnapshot and
EvidenceRef rather than an article-local citation number.

### Progressive repository access

GitMCP and GitHub MCP expose small repository/code/document/Issue/PR reads
instead of injecting a repository wholesale. GitHub MCP also demonstrates
toolset scoping, pagination and API error boundaries. ROSClaw's adapters and
MCP facade are read-only, size-limited and paginated. Research workers never
receive checkout hooks, package-install, shell, repository-write or GitHub
write authority.

DeepWiki's useful product shape is three levels: inspect structure, open a
page, then ask a scoped question. The Know v2 Project Wiki uses the same
progressive disclosure, with pinned source evidence added as a mandatory
ROSClaw constraint.

### Version-aware documentation

Context7 treats the library identifier and requested version as first-class
query inputs and returns bounded documentation/code examples. ROSClaw stores
document version, commit/tag/timestamp and compatibility fields. “Latest” is
never persisted as an unqualified fact; offline bundles explicitly report
that freshness cannot be guaranteed.

### Relations and incremental updates

GraphRAG's typed entity/relationship/claim model and local/global/DRIFT query
styles support the target relation-expansion design. ROSClaw implements the
relation model in SeekDB instead of adding a graph database.

LightRAG tracks document processing status, supports multiple chunking paths
and incremental insertion/update/delete. ROSClaw borrows content-hash delta
selection and affected-page/unit recompilation. It does not use LightRAG as a
storage layer.

### SeekDB

The locked pyseekdb revision exposes a common embedded/server Client,
collections/namespaces, metadata filters, full-text branches, dense/sparse
vectors and `hybrid_search(..., rank={"rrf": {}})`. Its tests distinguish
collection and namespace capabilities and cover hybrid filter rewrites.

Implementation consequences:

- capability probing happens at startup and is included in health output;
- repository semantics remain the same across modes, but unavailable native
  features produce named deterministic fallbacks;
- exact error/symbol matching precedes NGRAM/BM25/vector recall;
- score breakdowns retain every branch and whether native RRF/rerank ran;
- vector dimensions come from the active index-version record;
- server SQL migrations are packaged, while the lightweight compatibility
  backend provides the same store contract for CI/offline use;
- Know and Memory database/path equality is a startup error.

## Explicit non-adoptions

- No second primary vector or graph database.
- No unbounded recursive agent or unlimited network research.
- No external source instruction becomes a trusted prompt or tool call.
- No repository code is executed during ingestion.
- No generated statement without a real evidence reference enters a
  Reference Pack.
- No use/open/useful feedback is promoted into physical verification.
