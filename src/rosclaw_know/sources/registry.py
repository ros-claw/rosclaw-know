"""Source-adapter registry with explicit optional-service degradation."""

from __future__ import annotations

from .arxiv import ArxivAdapter
from .base import SourceAdapter, UnavailableAdapter
from .github import GitHubAdapter
from .official_docs import OfficialDocsAdapter, OfficialDocumentSpec


def default_source_registry(
    *, official_catalog: list[OfficialDocumentSpec] | None = None
) -> dict[str, SourceAdapter]:
    return {
        "github": GitHubAdapter(),
        "official_docs": OfficialDocsAdapter(official_catalog or []),
        "arxiv": ArxivAdapter(),
        "deepwiki": UnavailableAdapter("deepwiki", "MCP endpoint not configured"),
        "gitmcp": UnavailableAdapter("gitmcp", "MCP endpoint not configured"),
        "context7": UnavailableAdapter("context7", "MCP endpoint not configured"),
        "web": UnavailableAdapter("web", "search provider API key not configured"),
    }
