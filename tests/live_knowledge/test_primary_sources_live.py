"""Opt-in pinned GitHub and arXiv primary-source acceptance."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from rosclaw_know.contracts import ResearchRequestV2, SourceRecordV2
from rosclaw_know.sources import ArxivAdapter, GitHubAdapter, SourceCandidate

pytestmark = pytest.mark.skipif(
    os.environ.get("ROSCLAW_RUN_LIVE_KNOWLEDGE") != "1"
    and os.environ.get("ROSCLAW_KNOW_LIVE_PRIMARY") != "1",
    reason="set ROSCLAW_RUN_LIVE_KNOWLEDGE=1 to run primary-source acceptance",
)


@pytest.mark.asyncio
async def test_live_github_snapshot_is_commit_pinned_and_read_only() -> None:
    repository = "ros-claw/rosclaw"
    url = f"https://github.com/{repository}"
    candidate = SourceCandidate(
        source=SourceRecordV2(
            source_id="live-rosclaw",
            canonical_url=url,
            source_type="repository",
            title="rosclaw",
            publisher="ros-claw",
            repository=repository,
            trust_tier="primary",
            discovered_at=datetime.now(UTC),
        ),
        adapter="github",
        snapshot_ref="HEAD",
        authority_score=1.0,
        qualification_score=1.0,
        metadata={"full_name": repository, "default_branch": "HEAD"},
    )
    adapter = GitHubAdapter(max_documents=5, timeout=30.0)
    snapshot = await adapter.snapshot(candidate)
    documents = [document async for document in adapter.fetch_documents(snapshot)]
    assert len(snapshot.commit_sha or "") == 40
    assert snapshot.version_value == snapshot.commit_sha
    assert snapshot.integrity.sha256 == snapshot.content_hash
    assert documents
    assert all(document.metadata.get("code_executed") is False for document in documents)
    assert all(snapshot.commit_sha in document.metadata["url"] for document in documents[:1])


@pytest.mark.asyncio
async def test_live_arxiv_robonaldo_is_version_pinned() -> None:
    request = ResearchRequestV2(
        request_id="live-robonaldo",
        topic="RoboNaldo arXiv 2606.11092",
        goal="pin current paper metadata and abstract",
        depth="shallow",
        max_sources=8,
        token_budget=20_000,
    )
    adapter = ArxivAdapter(timeout=30.0)
    candidate = (await adapter.discover(request))[0]
    snapshot = await adapter.snapshot(candidate)
    documents = [document async for document in adapter.fetch_documents(snapshot)]
    assert snapshot.version_value.startswith("2606.11092v")
    assert snapshot.integrity.sha256 == snapshot.content_hash
    assert documents and documents[0].document_type == "paper_abstract"
    assert documents[0].snapshot_id == snapshot.snapshot_id
    assert documents[0].metadata["code_executed"] is False
