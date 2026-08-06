"""Bounded read-only source-adapter contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import Field

from rosclaw_know.contracts import ResearchRequestV2, SourceRecordV2, SourceSnapshotV2
from rosclaw_know.contracts.base import StrictContract
from rosclaw_know.store import DocumentRecord


class SourceUnavailableError(RuntimeError):
    """A source cannot be reached or is not configured; callers may degrade."""


class SourceLimitError(ValueError):
    """A source exceeded its declared bounded-ingestion limits."""


class SourceCandidate(StrictContract):
    source: SourceRecordV2
    adapter: str
    snapshot_ref: str | None = None
    authority_score: float = Field(default=0.5, ge=0.0, le=1.0)
    qualification_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceAdapter(Protocol):
    name: str

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]: ...

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2: ...

    def fetch_documents(self, snapshot: SourceSnapshotV2) -> AsyncIterator[DocumentRecord]: ...


class UnavailableAdapter:
    """Explicit placeholder for optional externally hosted MCP adapters."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        return []

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        raise SourceUnavailableError(f"{self.name} unavailable: {self.reason}")

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        raise SourceUnavailableError(f"{self.name} unavailable: {self.reason}")
        yield  # pragma: no cover - establishes AsyncIterator shape
