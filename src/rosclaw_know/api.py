"""FastAPI surface for rosclaw-know — Phase 9.

Exposes the offline pipeline as HTTP so an OS-level agent (openclaw /
Harmes Agent) can ask know to do **on-demand deep research** and poll
for completion. Endpoints today:

    POST  /know/v1/research            — start a job
    GET   /know/v1/research/{id}       — poll status
    GET   /know/v1/research            — list recent jobs
    POST  /know/v1/task-pack/build     — Sprint 7: pre-flight task pack

Plus the standard ``GET /healthz`` for liveness.

The server is intentionally minimal — heavy lifting is in
``research_worker.py`` + ``research_sources.py``. This file is just
plumbing.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .config import ASSETS_DIR
from .contracts import (
    PUBLIC_CONTRACTS,
    KnowledgeUsageFeedbackV1,
    ReferenceContextV2,
    ResearchRequestV2,
    StrictContract,
    export_contract_schemas,
)
from .research_store import ResearchStore
from .research_worker import ResearchWorker
from .retrieval import ReferencePackBuilder
from .schemas import (
    ArtifactType,
    ObjectiveDirection,
    TaskPackQuery,
)
from .sources import ResearchOrchestrator, build_research_plan, default_source_registry
from .store import KnowStore, create_know_store
from .task_pack_builder import TaskCardNotFoundError, build_task_pack

logger = logging.getLogger("rosclaw_know.api")

_store: ResearchStore | None = None
_worker: ResearchWorker | None = None
_task_pack_assets: dict | None = None
_v2_store: KnowStore | None = None
"""Sprint 7 cache: ``{"tasks": list[TaskCard], "patterns": list[PatternCardV2],
"failures": list[FailureMode]}``.  Populated by the lifespan hook so
the task-pack endpoint doesn't pay the YAML parse cost per request."""


def _get_store() -> ResearchStore:
    if _store is None:
        raise RuntimeError("ResearchStore not initialized (lifespan didn't run)")
    return _store


def _get_worker() -> ResearchWorker:
    if _worker is None:
        raise RuntimeError("ResearchWorker not initialized (lifespan didn't run)")
    return _worker


def _get_task_pack_assets() -> dict:
    if _task_pack_assets is None:
        raise RuntimeError(
            "Task-pack assets not loaded — lifespan didn't run, or YAMLs are missing"
        )
    return _task_pack_assets


def _try_load_task_pack_assets() -> dict | None:
    """Best-effort lazy load of the task-pack YAMLs.

    Thin wrapper over :func:`rosclaw_know.asset_loader.load_task_pack_assets`
    that reads ``config.ASSETS_DIR`` at call time so test fixtures that
    monkey-patch the config after import still take effect.
    """
    from . import config as _config
    from .asset_loader import load_task_pack_assets

    result = load_task_pack_assets(_config.ASSETS_DIR)
    if result is None:
        logger.warning(
            "Task-pack assets incomplete in %s; /know/v1/task-pack/build will 503",
            _config.ASSETS_DIR,
        )
    return result


def configure_v2_store(store: KnowStore | None) -> None:
    """Inject a v2 store for an embedding host or deterministic API test."""

    global _v2_store
    _v2_store = store


def _get_v2_store() -> KnowStore:
    if _v2_store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Know v2 store is disabled; configure ROSCLAW_KNOW_STORE_MODE",
        )
    return _v2_store


def _build_v2_store_from_env() -> KnowStore | None:
    mode = os.environ.get("ROSCLAW_KNOW_STORE_MODE", "disabled").casefold()
    if mode == "disabled":
        return None
    common = {
        "database": os.environ.get("ROSCLAW_KNOW_DATABASE", "rosclaw_know"),
        "memory_database": os.environ.get("ROSCLAW_MEMORY_DATABASE"),
        "practice_database": os.environ.get("ROSCLAW_PRACTICE_DATABASE"),
        "memory_path": os.environ.get("ROSCLAW_MEMORY_SEEKDB_PATH"),
        "practice_path": os.environ.get("ROSCLAW_PRACTICE_SEEKDB_PATH"),
    }
    if mode == "memory":
        return create_know_store(
            mode=mode,
            allow_test_memory=os.environ.get("ROSCLAW_KNOW_ALLOW_TEST_MEMORY") == "1",
        )
    if mode == "embedded":
        from .config import RUNTIME_DATA_DIR

        path = os.environ.get(
            "ROSCLAW_KNOW_SEEKDB_PATH", str(RUNTIME_DATA_DIR / "know" / "seekdb")
        )
        return create_know_store(mode=mode, path=path, **common)
    return create_know_store(
        mode=mode,
        host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEEKDB_PORT", "2881")),
        tenant=os.environ.get("SEEKDB_TENANT", "sys"),
        user=os.environ.get("SEEKDB_USER", "root"),
        password=os.environ.get("SEEKDB_PASSWORD", ""),
        **common,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _store, _worker, _task_pack_assets, _v2_store
    owns_v2_store = False
    _store = ResearchStore.load()
    _worker = ResearchWorker(_store)
    _worker.start()
    # Always (re)load at lifespan entry — tests that monkey-patch
    # ``rosclaw_know.config.ASSETS_DIR`` between FastAPI app
    # instantiations want the freshly-pointed path.
    _task_pack_assets = _try_load_task_pack_assets()
    if _v2_store is None:
        _v2_store = _build_v2_store_from_env()
        owns_v2_store = _v2_store is not None
    logger.info(
        "rosclaw-know HTTP layer ready; %d jobs in store; task-pack assets %s",
        len(_store._jobs),
        "loaded" if _task_pack_assets is not None else "MISSING",
    )
    yield
    if _worker is not None:
        await _worker.stop()
    if owns_v2_store and _v2_store is not None:
        _v2_store.close()
        _v2_store = None


app = FastAPI(
    title="ROSClaw-Know — Research Service",
    version="1.2.0",
    description=(
        "Agent-callable deep-research and knowledge-mining service. "
        "Long-running research jobs run async; clients poll for completion."
    ),
    lifespan=_lifespan,
)


# ── schemas ───────────────────────────────────────────────────────────────


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=2, description="Free-form research topic.")
    depth: Literal["shallow", "deep"] = Field(
        "shallow",
        description=(
            "shallow ≈ 5–10 sources, fast. "
            "deep ≈ 15–25 sources, longer runtime + cost."
        ),
    )
    budget_tokens: int = Field(50_000, ge=1000, le=500_000)


class ResearchJobOut(BaseModel):
    job_id: str
    topic: str
    depth: str
    budget_tokens: int
    status: str
    created_at: str
    updated_at: str
    sources_planned: int
    sources_fetched: int
    clusters_added: int
    error: str | None = None
    summary: str | None = None
    new_cluster_ids: list[str] = []


# ── auth (very simple X-API-Key) ─────────────────────────────────────────


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """Protect v2 content endpoints when an operator configures an allowlist."""

    raw = os.environ.get("ROSCLAW_KNOW_API_KEYS", "").strip()
    keys = {key.strip() for key in raw.split(",") if key.strip()}
    if not keys:
        return x_api_key or ""
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    if x_api_key not in keys:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid API key")
    return x_api_key


# ── endpoints ─────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> JSONResponse:
    store = _get_store()
    v2 = _v2_store
    return JSONResponse({
        "status": "ok",
        "version": app.version,
        "jobs_in_store": len(store._jobs),
        "bridge_index_exists": (ASSETS_DIR / "bridge_index.json").exists(),
        "v2": {
            "enabled": v2 is not None,
            "capabilities": (
                v2.capabilities.model_dump(mode="json") if v2 is not None else None
            ),
        },
    })


class ReferencePackBuildRequest(StrictContract):
    query: str = Field(min_length=1, max_length=20_000)
    context: ReferenceContextV2
    top_k: int = Field(default=10, ge=1, le=100)
    token_budget: int = Field(default=8_000, ge=1, le=500_000)


@app.get("/know/v2/capabilities")
async def v2_capabilities() -> JSONResponse:
    store = _get_v2_store()
    return JSONResponse(
        {
            "schema_versions": sorted(
                contract.SCHEMA_VERSION
                for contract in PUBLIC_CONTRACTS
                if contract.SCHEMA_VERSION is not None
            ),
            "store": store.capabilities.model_dump(mode="json"),
        }
    )


@app.get("/know/v2/health")
async def v2_health() -> JSONResponse:
    store = _v2_store
    if store is None:
        return JSONResponse(
            {
                "status": "disabled",
                "service_version": app.version,
                "schema_version": "know.v2",
                "seekdb_connected": False,
            }
        )
    index = store.latest_index_version()
    return JSONResponse(
        {
            "status": "ok",
            "service_version": app.version,
            "schema_version": "know.v2",
            "seekdb_mode": store.capabilities.backend,
            "seekdb_connected": True,
            "index_version": index.index_version if index else "unversioned",
            **store.statistics(),
            "capabilities": store.capabilities.model_dump(mode="json"),
        }
    )


@app.get("/know/v2/contracts")
async def v2_contracts() -> JSONResponse:
    return JSONResponse(export_contract_schemas(PUBLIC_CONTRACTS))


@app.post("/know/v2/research/plan", dependencies=[Depends(require_api_key)])
async def v2_research_plan(request: ResearchRequestV2) -> JSONResponse:
    return JSONResponse(build_research_plan(request).model_dump(mode="json"))


@app.post("/know/v2/research", dependencies=[Depends(require_api_key)])
async def v2_research(request: ResearchRequestV2) -> JSONResponse:
    store = _get_v2_store()
    orchestrator = ResearchOrchestrator(store, default_source_registry())
    result = await orchestrator.run(request)
    return JSONResponse(result.model_dump(mode="json"))


@app.post("/know/v2/reference-packs", dependencies=[Depends(require_api_key)])
@app.post("/know/v2/reference-packs/build", dependencies=[Depends(require_api_key)])
@app.post("/know/v2/retrieve", dependencies=[Depends(require_api_key)])
async def v2_build_reference_pack(request: ReferencePackBuildRequest) -> JSONResponse:
    pack = ReferencePackBuilder(_get_v2_store()).retrieve(
        query=request.query,
        context=request.context,
        top_k=request.top_k,
        token_budget=request.token_budget,
    )
    return JSONResponse(pack.model_dump(mode="json"))


@app.get(
    "/know/v2/reference-packs/{reference_pack_id}", dependencies=[Depends(require_api_key)]
)
async def v2_get_reference_pack(reference_pack_id: str) -> JSONResponse:
    pack = _get_v2_store().get_reference_pack(reference_pack_id)
    if pack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reference pack not found")
    return JSONResponse(pack.model_dump(mode="json"))


@app.get("/know/v2/sources/{source_id}", dependencies=[Depends(require_api_key)])
async def v2_get_source(source_id: str) -> JSONResponse:
    source = _get_v2_store().get_source(source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    return JSONResponse(source.model_dump(mode="json"))


@app.get("/know/v2/snapshots/{snapshot_id}", dependencies=[Depends(require_api_key)])
async def v2_get_snapshot(snapshot_id: str) -> JSONResponse:
    snapshot = _get_v2_store().get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "snapshot not found")
    return JSONResponse(snapshot.model_dump(mode="json"))


@app.get("/know/v2/projects/{project_id}", dependencies=[Depends(require_api_key)])
async def v2_get_project(project_id: str) -> JSONResponse:
    card = _get_v2_store().get_project_card(project_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return JSONResponse(card.model_dump(mode="json"))


@app.get("/know/v2/projects/{project_id}/wiki", dependencies=[Depends(require_api_key)])
async def v2_get_project_wiki(project_id: str) -> JSONResponse:
    store = _get_v2_store()
    card = store.get_project_card(project_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return JSONResponse(
        {
            "project": card.model_dump(mode="json"),
            "components": [item.model_dump(mode="json") for item in store.list_components(project_id)],
            "pages": [item.model_dump(mode="json") for item in store.list_wiki_pages(project_id)],
        }
    )


@app.get("/know/v2/wiki/pages/{page_id}", dependencies=[Depends(require_api_key)])
async def v2_get_wiki_page(page_id: str) -> JSONResponse:
    page = _get_v2_store().get_wiki_page(page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "wiki page not found")
    return JSONResponse(page.model_dump(mode="json"))


@app.get("/know/v2/evidence/{evidence_id}", dependencies=[Depends(require_api_key)])
async def v2_get_evidence(evidence_id: str) -> JSONResponse:
    evidence = _get_v2_store().get_evidence(evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence not found")
    return JSONResponse(evidence.model_dump(mode="json"))


@app.post("/know/v2/feedback", dependencies=[Depends(require_api_key)])
async def v2_feedback(request: Request) -> JSONResponse:
    try:
        feedback = KnowledgeUsageFeedbackV1.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    created = _get_v2_store().put_feedback(feedback)
    return JSONResponse(
        {"feedback_id": feedback.feedback_id, "created": created},
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@app.post("/know/v1/research", status_code=status.HTTP_202_ACCEPTED)
async def start_research(req: ResearchRequest) -> JSONResponse:
    store = _get_store()
    worker = _get_worker()
    job = store.create(req.topic, req.depth, req.budget_tokens)
    worker.submit(job)
    return JSONResponse(
        ResearchJobOut(**{k: v for k, v in job.__dict__.items()}).model_dump(),
        status_code=status.HTTP_202_ACCEPTED,
    )


@app.get("/know/v1/research/{job_id}")
async def get_research(job_id: str) -> JSONResponse:
    store = _get_store()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"job {job_id!r} not found")
    return JSONResponse(ResearchJobOut(**job.__dict__).model_dump())


@app.get("/know/v1/research")
async def list_research(limit: int = 20) -> JSONResponse:
    store = _get_store()
    jobs = store.list_recent(limit=max(1, min(200, limit)))
    return JSONResponse([ResearchJobOut(**j.__dict__).model_dump() for j in jobs])


# ── Sprint 7: task-pack endpoint ─────────────────────────────────────────


class TaskPackRequest(BaseModel):
    """Plan §10.1 input — the HTTP-facing form of :class:`TaskPackQuery`.

    Mirrors the request schema in the plan verbatim so agents that read
    the spec can hit the endpoint without translation.
    """

    task_name: str = Field(..., min_length=1, max_length=120)
    benchmark: str | None = Field(None, max_length=120)
    artifact_language: ArtifactType | None = None
    objective_direction: ObjectiveDirection | None = None
    metric_name: str | None = None
    budget_iterations: int = Field(20, ge=1, le=1_000_000)
    top_k_patterns: int = Field(5, ge=1, le=50)
    max_tokens: int = Field(1200, ge=200, le=8000)


@app.post("/know/v1/task-pack/build")
async def build_task_pack_endpoint(req: TaskPackRequest) -> JSONResponse:
    """Sprint 7 / plan §10: build a pre-flight task pack for the agent.

    Returns:
      200 + TaskPack JSON on success.
      404 when no TaskCard matches the request.
      503 when the asset YAMLs haven't been loaded (early bootstrap).
    """
    if _task_pack_assets is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Task-pack assets not loaded — run scripts/build_physical_graph.py first.",
        )
    assets = _get_task_pack_assets()
    query = TaskPackQuery(
        task_name=req.task_name,
        benchmark=req.benchmark,
        artifact_language=req.artifact_language,
        objective_direction=req.objective_direction,
        metric_name=req.metric_name,
        budget_iterations=req.budget_iterations,
        top_k_patterns=req.top_k_patterns,
        max_tokens=req.max_tokens,
    )
    t0 = time.perf_counter()
    try:
        pack = build_task_pack(
            query,
            catalog=assets["tasks"],
            patterns=assets["patterns"],
            failures=assets["failures"],
        )
    except TaskCardNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    payload = pack.model_dump(mode="json")
    payload["build_latency_ms"] = round(elapsed_ms, 2)
    return JSONResponse(payload)


# ── entrypoint ────────────────────────────────────────────────────────────


def run() -> None:
    """`rosclaw-know-server` console-script entry."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    host = os.environ.get("ROSCLAW_KNOW_HOST", "127.0.0.1")
    port = int(os.environ.get("ROSCLAW_KNOW_PORT", "47821"))
    uvicorn.run("rosclaw_know.api:app", host=host, port=port, log_level="info")


__all__ = ["app", "configure_v2_store", "run"]
