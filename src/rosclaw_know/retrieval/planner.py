"""Structured retrieval planning before recall."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from rosclaw_know.contracts import ReferenceContextV2
from rosclaw_know.contracts.base import StrictContract
from rosclaw_know.store import SearchFilters

_EXACT_RE = re.compile(
    r"(?:\b(?:[A-Z][A-Z0-9_]{2,}|[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+|"
    r"[A-Za-z]+Error|(?:0x)?[0-9A-F]{4,})\b|(?<!\w)-\d+\b)"
)


class RetrievalPlan(StrictContract):
    query: str
    query_profile: Literal[
        "PROFILE_ERROR", "PROFILE_CODE", "PROFILE_CONCEPT", "PROFILE_PROJECT"
    ]
    retrieval_lanes: list[str] = Field(min_length=1)
    exact_terms: list[str] = Field(default_factory=list)
    ngram_terms: list[str] = Field(default_factory=list)
    semantic_queries: dict[str, str]
    filters: SearchFilters
    recall_limit: int = Field(ge=1, le=1000)
    relation_hops: int = Field(default=1, ge=0, le=2)


def build_retrieval_plan(
    query: str, context: ReferenceContextV2, *, requested_limit: int = 10
) -> RetrievalPlan:
    exact_terms = list(dict.fromkeys(_EXACT_RE.findall(query)))
    ngrams = [
        term for term in re.findall(r"[A-Za-z0-9_./:-]{4,}", query) if term not in exact_terms
    ]
    folded = f"{query} {context.current_failure or ''}".casefold()
    if context.current_failure or exact_terms or re.search(r"\b(error|exception|failed|timeout)\b", folded):
        profile = "PROFILE_ERROR"
        lanes = ["exact", "ngram", "bm25", "compatibility", "vector", "relation"]
    elif re.search(
        r"\b(file|module|class|symbol|config|entrypoint|implementation|where)\b|[/\\]", folded
    ):
        profile = "PROFILE_CODE"
        lanes = ["symbol", "path", "bm25", "code_vector", "component_relation"]
    elif re.search(r"\b(project|repository|repo|paper|projects)\b|项目|论文", folded):
        profile = "PROFILE_PROJECT"
        lanes = ["project_metadata", "problem_vector", "wiki", "relation", "authority"]
    else:
        profile = "PROFILE_CONCEPT"
        lanes = ["mechanism_vector", "content_vector", "bm25", "relation"]
    filters = SearchFilters(
        robot=context.robot,
        simulator=context.simulator,
        ros_distro=context.ros_distro,
    )
    stage = f" Current stage: {context.current_stage}." if context.current_stage else ""
    failure = f" Failure: {context.current_failure}." if context.current_failure else ""
    return RetrievalPlan(
        query=query,
        query_profile=profile,  # type: ignore[arg-type]
        retrieval_lanes=lanes,
        exact_terms=exact_terms,
        ngram_terms=ngrams[:20],
        semantic_queries={
            "problem": f"{query}{failure}",
            "mechanism": f"Mechanism explaining {query}",
            "content": f"Implementation reference for {query}{stage}",
            "code": " ".join(exact_terms + ngrams[:10]) or query,
        },
        filters=filters,
        recall_limit=min(1000, max(requested_limit * 4, 20)),
        relation_hops=1,
    )
