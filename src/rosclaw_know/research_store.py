"""In-memory + jsonl-backed store for /research tasks.

Phase 9 adds an HTTP /research endpoint that fires off long-running
asynchronous research jobs. Agents poll for completion. We need a tiny
store that:

  * survives `uvicorn --reload` restarts (jsonl on disk)
  * supports concurrent worker writes + reader queries
  * exposes a small synchronous API (no threading primitives leak)

This is intentionally not a database — research jobs are short-lived
(minutes to hours) and the tail-end of the jsonl is the source of truth.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .config import DATA_DIR

logger = logging.getLogger("rosclaw_know.research_store")

ResearchStatus = Literal["queued", "running", "completed", "failed"]

DEFAULT_STORE_PATH = DATA_DIR / "research_jobs.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class ResearchJob:
    job_id: str
    topic: str
    depth: str  # "shallow" | "deep"
    budget_tokens: int
    status: ResearchStatus
    created_at: str
    updated_at: str
    sources_planned: int = 0
    sources_fetched: int = 0
    clusters_added: int = 0
    error: str | None = None
    summary: str | None = None
    new_cluster_ids: list[str] = field(default_factory=list)


@dataclass
class ResearchStore:
    """Append-only jsonl + in-RAM dict. Thread-safe under a single lock."""

    path: Path = field(default_factory=lambda: DEFAULT_STORE_PATH)
    _jobs: dict[str, ResearchJob] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def load(cls, path: Path | None = None) -> ResearchStore:
        path = path or DEFAULT_STORE_PATH
        store = cls(path=path)
        if not path.exists():
            return store
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                    job = ResearchJob(**rec)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Skipping malformed line: %s", exc)
                    continue
                # Later lines override earlier — that's the append-only
                # "last write wins" semantics we want.
                store._jobs[job.job_id] = job
        logger.info("Loaded %d research job(s) from %s", len(store._jobs), path)
        return store

    def _append(self, job: ResearchJob) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(job), ensure_ascii=False) + "\n")

    def create(self, topic: str, depth: str, budget_tokens: int) -> ResearchJob:
        with self._lock:
            job = ResearchJob(
                job_id=_new_id(),
                topic=topic,
                depth=depth,
                budget_tokens=budget_tokens,
                status="queued",
                created_at=_now(),
                updated_at=_now(),
            )
            self._jobs[job.job_id] = job
            self._append(job)
            return job

    def get(self, job_id: str) -> ResearchJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[ResearchJob]:
        with self._lock:
            ordered = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return ordered[:limit]

    def update(self, job_id: str, **changes: object) -> ResearchJob | None:
        with self._lock:
            cur = self._jobs.get(job_id)
            if cur is None:
                return None
            new = replace(cur, updated_at=_now(), **changes)  # type: ignore[arg-type]
            self._jobs[job_id] = new
            self._append(new)
            return new


__all__ = [
    "DEFAULT_STORE_PATH",
    "ResearchJob",
    "ResearchStatus",
    "ResearchStore",
]
