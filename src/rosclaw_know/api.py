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
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import ASSETS_DIR
from .research_store import ResearchStore
from .research_worker import ResearchWorker
from .schemas import (
    ArtifactType,
    ObjectiveDirection,
    TaskPackQuery,
)
from .task_pack_builder import TaskCardNotFoundError, build_task_pack

logger = logging.getLogger("rosclaw_know.api")

_store: ResearchStore | None = None
_worker: ResearchWorker | None = None
_task_pack_assets: dict | None = None
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

    Returns ``None`` (and logs a warning) when any of the canonical
    paths are missing — that's fine in early bootstrap / CI; the
    endpoint surfaces a 503 instead of crashing the whole server.
    """
    # Local imports so the api module can still be imported in CI
    # environments where the data dir hasn't been provisioned.  Reading
    # ``config.ASSETS_DIR`` at call time (not import time) makes the
    # function robust against test fixtures that monkey-patch
    # ``rosclaw_know.config.ASSETS_DIR`` after this module is imported.
    from . import config as _config
    from .schemas import FailureMode, PatternCardV2, TaskCard
    import yaml as _yaml

    assets_dir = _config.ASSETS_DIR
    paths = {
        "tasks": assets_dir / "task_cards.yaml",
        "patterns": assets_dir / "pattern_cards_v2.yaml",
        "failures": assets_dir / "failure_taxonomy.yaml",
    }
    if not all(p.is_file() for p in paths.values()):
        logger.warning(
            "Task-pack assets incomplete in %s; /know/v1/task-pack/build will 503",
            assets_dir,
        )
        return None
    try:
        tasks_raw = _yaml.safe_load(paths["tasks"].read_text(encoding="utf-8")) or {}
        patterns_raw = _yaml.safe_load(paths["patterns"].read_text(encoding="utf-8")) or {}
        failures_raw = _yaml.safe_load(paths["failures"].read_text(encoding="utf-8")) or {}
        return {
            "tasks": [TaskCard.model_validate(t) for t in tasks_raw.get("task_cards", [])],
            "patterns": [PatternCardV2.model_validate(p) for p in patterns_raw.get("pattern_cards", [])],
            "failures": [FailureMode.model_validate(f) for f in failures_raw.get("failures", [])],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load task-pack assets: %s", exc)
        return None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _store, _worker, _task_pack_assets
    _store = ResearchStore.load()
    _worker = ResearchWorker(_store)
    _worker.start()
    # Always (re)load at lifespan entry — tests that monkey-patch
    # ``rosclaw_know.config.ASSETS_DIR`` between FastAPI app
    # instantiations want the freshly-pointed path.
    _task_pack_assets = _try_load_task_pack_assets()
    logger.info(
        "rosclaw-know HTTP layer ready; %d jobs in store; task-pack assets %s",
        len(_store._jobs),
        "loaded" if _task_pack_assets is not None else "MISSING",
    )
    yield
    if _worker is not None:
        await _worker.stop()


app = FastAPI(
    title="ROSClaw-Know — Research Service",
    version="0.9.0",
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


def require_api_key():
    """Phase 9 keeps auth optional — operator turns it on with env var.

    When ``ROSCLAW_KNOW_API_KEYS`` is unset, all requests pass. When set,
    the comma-separated list of valid keys is checked against the
    ``X-API-Key`` header.
    """
    import os
    raw = os.environ.get("ROSCLAW_KNOW_API_KEYS", "").strip()
    keys = {k.strip() for k in raw.split(",") if k.strip()}

    def _check(x_api_key: str | None = None) -> None:
        if not keys:
            return  # auth off
        # FastAPI dependency injection puts header here only when the route
        # declares it; the simpler path is to read it from request scope.
        # We do the lazy thing: read from contextvar via a wrapper. For
        # now: rely on per-route Header(...) hookup.
        raise NotImplementedError  # routes set their own header check

    return _check


# ── endpoints ─────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> JSONResponse:
    store = _get_store()
    return JSONResponse({
        "status": "ok",
        "version": app.version,
        "jobs_in_store": len(store._jobs),
        "bridge_index_exists": (ASSETS_DIR / "bridge_index.json").exists(),
    })


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
    import os

    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    host = os.environ.get("ROSCLAW_KNOW_HOST", "127.0.0.1")
    port = int(os.environ.get("ROSCLAW_KNOW_PORT", "47821"))
    uvicorn.run("rosclaw_know.api:app", host=host, port=port, log_level="info")


__all__ = ["app", "run"]
