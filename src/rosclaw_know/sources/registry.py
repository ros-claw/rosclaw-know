"""Source-adapter registry with explicit optional-service degradation."""

from __future__ import annotations

import os

from .arxiv import ArxivAdapter
from .base import SourceAdapter, UnavailableAdapter
from .external_mcp import Context7Adapter, DeepWikiPublicAdapter, GitMCPAdapter
from .github import GitHubAdapter
from .official_docs import OfficialDocsAdapter, OfficialDocumentSpec


def default_source_registry(
    *, official_catalog: list[OfficialDocumentSpec] | None = None
) -> dict[str, SourceAdapter]:
    external_enabled = os.environ.get("ROSCLAW_KNOW_EXTERNAL_MCP", "1") != "0"
    registry: dict[str, SourceAdapter] = {
        "github": GitHubAdapter(),
        "official_docs": OfficialDocsAdapter(official_catalog or []),
        "arxiv": ArxivAdapter(),
        "web": UnavailableAdapter("web", "search provider API key not configured"),
    }
    if external_enabled:
        registry.update(
            deepwiki=DeepWikiPublicAdapter(
                endpoint=os.environ.get(
                    "ROSCLAW_DEEPWIKI_MCP_URL", "https://mcp.deepwiki.com/mcp"
                )
            ),
            gitmcp=GitMCPAdapter(
                endpoint_template=os.environ.get(
                    "ROSCLAW_GITMCP_URL_TEMPLATE", "https://gitmcp.io/{repository}"
                )
            ),
            context7=Context7Adapter(
                endpoint=os.environ.get(
                    "ROSCLAW_CONTEXT7_MCP_URL", "https://mcp.context7.com/mcp"
                )
            ),
        )
    else:
        registry.update(
            deepwiki=UnavailableAdapter("deepwiki", "external MCP disabled"),
            gitmcp=UnavailableAdapter("gitmcp", "external MCP disabled"),
            context7=UnavailableAdapter("context7", "external MCP disabled"),
        )
    return registry
