"""Storage-neutral models for the Know v2 repository boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from rosclaw_know.contracts.base import StrictContract
from rosclaw_know.contracts.source_v2 import EvidenceRefV2


class StoreCapabilities(StrictContract):
    backend: Literal["seekdb_embedded", "seekdb_server", "memory"]
    fulltext: bool
    hybrid_search: bool
    sql_join: bool
    ai_rerank: bool
    multi_vector: bool
    transactions: Literal["native", "single_record", "copy_on_write"]
    degraded: list[str] = Field(default_factory=list)


class DocumentRecord(StrictContract):
    document_id: str
    snapshot_id: str
    document_type: str
    path: str
    title: str
    language: str | None = None
    content: str
    content_hash: str
    mime_type: str = "text/plain"
    size_bytes: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RelationRecord(StrictContract):
    relation_id: str
    from_id: str
    from_type: str
    relation_type: Literal[
        "IMPLEMENTS",
        "EXTENDS",
        "ALTERNATIVE_TO",
        "DEPENDS_ON",
        "FIXES",
        "CONTRADICTS",
        "APPLIES_TO",
        "INCOMPATIBLE_WITH",
        "DERIVED_FROM",
        "SUPERSEDES",
        "VALIDATED_BY",
        "DOCUMENTS",
        "MENTIONS",
    ]
    to_id: str
    to_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_id: str
    created_at: datetime


class ProjectComponentRecord(StrictContract):
    component_id: str
    project_id: str
    snapshot_id: str
    parent_component_id: str | None = None
    component_type: str
    path: str
    language: str | None = None
    responsibility: str
    public_symbols: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    content_hash: str


class WikiPageRecord(StrictContract):
    page_id: str
    snapshot_id: str
    project_id: str
    parent_page_id: str | None = None
    page_type: str
    title: str
    slug: str
    summary: str
    content: str
    outline_order: int = Field(ge=0)
    content_hash: str
    evidence_refs: list[EvidenceRefV2] = Field(min_length=1)
    created_at: datetime


class IndexVersionRecord(StrictContract):
    index_version: str
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    reranker_model: str | None = None
    schema_version: str
    source_snapshot_hash: str
    created_at: datetime


class SearchFilters(StrictContract):
    unit_types: list[str] = Field(default_factory=list)
    snapshot_ids: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=lambda: ["verified", "draft"])
    robot: str | None = None
    simulator: str | None = None
    ros_distro: str | None = None


class SearchHit(StrictContract):
    knowledge_unit_id: str
    score: float
    score_breakdown: dict[str, float]
    matched_by: list[str]
    warnings: list[str] = Field(default_factory=list)
