"""Pinned arXiv metadata/abstract adapter (no PDF execution or parsing)."""

from __future__ import annotations

import asyncio
import hashlib
import urllib.parse
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

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

_API = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom"}


class ArxivAdapter:
    name = "arxiv"

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        timeout: float = 15.0,
        max_response_bytes: int = 3_000_000,
    ) -> None:
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._candidates: dict[str, SourceCandidate] = {}

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        query = urllib.parse.urlencode(
            {
                "search_query": f"all:{request.topic}",
                "max_results": min(request.max_sources, 50),
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
        response = await asyncio.to_thread(
            self.transport.get,
            f"{_API}?{query}",
            headers={"User-Agent": "rosclaw-know-v2/2", "Accept": "application/atom+xml"},
            timeout=self.timeout,
            max_bytes=self.max_response_bytes,
        )
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError as exc:
            raise SourceUnavailableError("arXiv returned malformed Atom XML") from exc
        candidates = []
        for entry in root.findall("a:entry", _NS):
            url = (entry.findtext("a:id", default="", namespaces=_NS) or "").strip()
            title = " ".join((entry.findtext("a:title", default="", namespaces=_NS) or "").split())
            published = (entry.findtext("a:published", default="", namespaces=_NS) or "").strip()
            version = url.rsplit("/", 1)[-1]
            if not url or not title or not version:
                continue
            abstract = " ".join(
                (entry.findtext("a:summary", default="", namespaces=_NS) or "").split()
            )
            authors = [
                name.text.strip() for name in entry.findall("a:author/a:name", _NS) if name.text
            ]
            candidate = SourceCandidate(
                source=SourceRecordV2(
                    source_id=_id("source", url.casefold()),
                    canonical_url=url,
                    source_type="paper",
                    title=title,
                    publisher="arXiv",
                    trust_tier="primary",
                    discovered_at=datetime.now(UTC),
                    tags=[],
                ),
                adapter=self.name,
                snapshot_ref=version,
                authority_score=0.85,
                qualification_score=0.6,
                metadata={"abstract": abstract, "authors": authors, "published": published},
            )
            candidates.append(candidate)
            self._candidates[candidate.source.source_id] = candidate
        return candidates

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        version = candidate.snapshot_ref or "unknown"
        text = str(candidate.metadata.get("abstract") or "")
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        return SourceSnapshotV2(
            snapshot_id=_id("snapshot", f"arxiv:{candidate.source.source_id}:{version}"),
            source_id=candidate.source.source_id,
            version_kind="document_version",
            version_value=version,
            published_at=_parse_time(str(candidate.metadata.get("published") or "")),
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            integrity=IntegrityV2(sha256=content_hash),
        )

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        candidate = self._candidates.get(snapshot.source_id)
        if candidate is None:
            raise SourceUnavailableError("arXiv candidate state unavailable")
        abstract, signals = normalize_untrusted_text(str(candidate.metadata.get("abstract") or ""))
        body = (
            f"# {candidate.source.title}\n\n"
            f"Authors: {', '.join(candidate.metadata.get('authors') or [])}\n\n"
            f"## Abstract\n\n{abstract}\n"
        )
        yield _document(
            snapshot,
            "arXiv",
            "abstract.md",
            body,
            "paper_abstract",
            url=candidate.source.canonical_url,
            prompt_injection_signals=signals,
        )


def _parse_time(value: str) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
