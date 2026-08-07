"""Opt-in public MCP acceptance; derived sources never become primary truth."""

from __future__ import annotations

import os

import pytest

from rosclaw_know.contracts import ResearchRequestV2
from rosclaw_know.sources import Context7Adapter, DeepWikiPublicAdapter, GitMCPAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("ROSCLAW_RUN_LIVE_KNOWLEDGE") != "1"
    and os.environ.get("ROSCLAW_KNOW_LIVE_MCP") != "1",
    reason="set ROSCLAW_RUN_LIVE_KNOWLEDGE=1 to run public MCP acceptance",
)


def request(topic: str, goal: str) -> ResearchRequestV2:
    return ResearchRequestV2(
        request_id="live-mcp-final",
        topic=topic,
        goal=goal,
        depth="shallow",
        max_sources=8,
        token_budget=20_000,
    )


async def _run(adapter, research_request):
    candidate = (await adapter.discover(research_request))[0]
    snapshot = await adapter.snapshot(candidate)
    documents = [document async for document in adapter.fetch_documents(snapshot)]
    assert documents
    assert snapshot.integrity.sha256 == snapshot.content_hash
    assert all(document.metadata["authority_tier"] == "B" for document in documents)
    assert all(document.metadata["code_executed"] is False for document in documents)
    assert all(
        document.metadata["evidence_policy"] == "derived_requires_pinned_primary_source"
        for document in documents
    )
    return candidate, snapshot, documents


@pytest.mark.asyncio
async def test_live_deepwiki_public() -> None:
    candidate, _, documents = await _run(
        DeepWikiPublicAdapter(timeout=60.0),
        request("ros-claw/rosclaw", "Where are Know and How adapters registered?"),
    )
    assert candidate.source.publisher == "DeepWiki"
    assert {document.path for document in documents} == {
        ".rosclaw/deepwiki/structure.md",
        ".rosclaw/deepwiki/contents.md",
        ".rosclaw/deepwiki/answer.md",
    }


@pytest.mark.asyncio
async def test_live_gitmcp_public() -> None:
    candidate, _, documents = await _run(
        GitMCPAdapter(timeout=60.0),
        request("ros-claw/rosclaw", "knowledge adapter entrypoints configuration"),
    )
    assert candidate.source.publisher == "GitMCP"
    assert any("code_search" in document.path for document in documents)
    assert any("documentation" in document.path for document in documents)


@pytest.mark.asyncio
async def test_live_context7_version_docs() -> None:
    candidate, _, documents = await _run(
        Context7Adapter(timeout=60.0),
        request("seekdb", "SeekDB 1.3 hybrid search and DBMS_HYBRID_SEARCH"),
    )
    assert candidate.source.publisher == "Context7"
    assert all(document.metadata["library_id"] for document in documents)
