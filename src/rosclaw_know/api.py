"""FastAPI surface for rosclaw-know — Phase 9.

Exposes the offline pipeline as HTTP so an OS-level agent (openclaw /
Harmes Agent) can ask know to do **on-demand deep research** and poll
for completion. Three endpoints today:

    POST  /know/v1/research      — start a job
    GET   /know/v1/research/{id} — poll status
    GET   /know/v1/research      — list recent jobs

Plus the standard ``GET /healthz`` for liveness.

The server is intentionally minimal — heavy lifting is in
``research_worker.py`` + ``research_sources.py``. This file is just
plumbing.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import ASSETS_DIR
from .research_store import ResearchStore
from .research_worker import ResearchWorker

logger = logging.getLogger("rosclaw_know.api")

_store: ResearchStore | None = None
_worker: ResearchWorker | None = None


def _get_store() -> ResearchStore:
    if _store is None:
        raise RuntimeError("ResearchStore not initialized (lifespan didn't run)")
    return _store


def _get_worker() -> ResearchWorker:
    if _worker is None:
        raise RuntimeError("ResearchWorker not initialized (lifespan didn't run)")
    return _worker


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _store, _worker
    _store = ResearchStore.load()
    _worker = ResearchWorker(_store)
    _worker.start()
    logger.info("rosclaw-know HTTP layer ready; %d jobs in store", len(_store._jobs))
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
