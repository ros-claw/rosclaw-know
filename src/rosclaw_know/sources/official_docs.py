"""Version-aware adapter for an allowlisted official-document catalog."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from rosclaw_know.contracts import (
    IntegrityV2,
    ResearchRequestV2,
    SourceRecordV2,
    SourceSnapshotV2,
)

from .base import SourceCandidate, SourceUnavailableError
from .github import _document, _id
from .http import HttpTransport, UrllibTransport
from .security import normalize_untrusted_text


@dataclass(frozen=True)
class OfficialDocumentSpec:
    title: str
    url: str
    publisher: str
    version: str | None = None
    license: str | None = None
    tags: tuple[str, ...] = ()


class OfficialDocsAdapter:
    name = "official_docs"

    def __init__(
        self,
        catalog: list[OfficialDocumentSpec],
        *,
        transport: HttpTransport | None = None,
        timeout: float = 15.0,
        max_document_bytes: int = 2_000_000,
    ) -> None:
        self.catalog = list(catalog)
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_document_bytes = max_document_bytes
        self._snapshots: dict[str, tuple[SourceCandidate, str, list[str], dict[str, str]]] = {}

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        terms = set(request.topic.casefold().split())
        ranked = []
        for spec in self.catalog:
            haystack = f"{spec.title} {' '.join(spec.tags)} {spec.publisher}".casefold()
            score = len(terms & set(haystack.split())) / max(1, len(terms))
            if score or not terms:
                ranked.append((score, spec))
        ranked.sort(key=lambda item: (-item[0], item[1].url))
        return [
            SourceCandidate(
                source=SourceRecordV2(
                    source_id=_id("source", spec.url.casefold()),
                    canonical_url=spec.url,
                    source_type="official_documentation",
                    title=spec.title,
                    publisher=spec.publisher,
                    license=spec.license,
                    trust_tier="official",
                    discovered_at=datetime.now(UTC),
                    tags=list(spec.tags),
                ),
                adapter=self.name,
                snapshot_ref=spec.version,
                authority_score=1.0,
                qualification_score=max(0.5, score),
                metadata={"version": spec.version},
            )
            for score, spec in ranked[: request.max_sources]
        ]

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        url = candidate.source.canonical_url
        if not url.startswith("https://"):
            raise ValueError("official documentation URL must use HTTPS")
        response = await asyncio.to_thread(
            self.transport.get,
            url,
            headers={"User-Agent": "rosclaw-know-v2/2", "Accept": "text/*,application/json"},
            timeout=self.timeout,
            max_bytes=self.max_document_bytes,
        )
        raw = response.body.decode("utf-8", errors="replace")
        content, signals = normalize_untrusted_text(raw)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        version = (
            candidate.snapshot_ref
            or response.headers.get("etag")
            or response.headers.get("last-modified")
            or content_hash[:16]
        )
        snapshot_id = _id("snapshot", f"official:{url}:{version}:{content_hash}")
        snapshot = SourceSnapshotV2(
            snapshot_id=snapshot_id,
            source_id=candidate.source.source_id,
            version_kind="document_version" if candidate.snapshot_ref else "timestamp",
            version_value=version,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            integrity=IntegrityV2(sha256=content_hash),
        )
        self._snapshots[snapshot_id] = (candidate, content, signals, response.headers)
        return snapshot

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        state = self._snapshots.get(snapshot.snapshot_id)
        if state is None:
            raise SourceUnavailableError(
                "snapshot state unavailable; snapshot and fetch must use the same adapter instance"
            )
        candidate, content, signals, headers = state
        content_type = headers.get("content-type", "text/plain").split(";", 1)[0]
        yield _document(
            snapshot,
            candidate.source.publisher or "official",
            "official-document",
            content,
            "official_documentation",
            url=candidate.source.canonical_url,
            prompt_injection_signals=signals,
        ).model_copy(update={"mime_type": content_type})
