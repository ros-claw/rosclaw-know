"""Know v2 discovery, qualification and deep-ingestion adapters."""

from .arxiv import ArxivAdapter
from .base import (
    SourceAdapter,
    SourceCandidate,
    SourceLimitError,
    SourceUnavailableError,
    UnavailableAdapter,
)
from .github import GitHubAdapter
from .official_docs import OfficialDocsAdapter, OfficialDocumentSpec
from .orchestrator import ResearchOrchestrator, ResearchRunResult
from .planner import ResearchPlan, ResearchSubquestion, build_research_plan
from .registry import default_source_registry

__all__ = [
    "ArxivAdapter",
    "GitHubAdapter",
    "OfficialDocsAdapter",
    "OfficialDocumentSpec",
    "ResearchPlan",
    "ResearchOrchestrator",
    "ResearchRunResult",
    "ResearchSubquestion",
    "SourceAdapter",
    "SourceCandidate",
    "SourceLimitError",
    "SourceUnavailableError",
    "UnavailableAdapter",
    "build_research_plan",
    "default_source_registry",
]
