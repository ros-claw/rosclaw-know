"""Repository protocol: the only supported persistence boundary for Know v2."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol

from rosclaw_know.contracts import (
    EvidenceRefV2,
    FeedbackGovernanceRecordV1,
    KnowledgeUnitV2,
    KnowledgeUsageFeedbackV1,
    ProjectCardV2,
    ReferencePackV2,
    SourceRecordV2,
    SourceSnapshotV2,
)

from .models import (
    DocumentRecord,
    IndexVersionRecord,
    ProjectComponentRecord,
    RelationRecord,
    SearchFilters,
    SearchHit,
    StoreCapabilities,
    WikiPageRecord,
)


class ImmutableSnapshotError(ValueError):
    """An existing snapshot ID was presented with different immutable data."""


class StoreConfigurationError(ValueError):
    """The configured Know store violates an isolation or capability guard."""


class KnowStore(Protocol):
    @property
    def capabilities(self) -> StoreCapabilities: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def upsert_source(self, source: SourceRecordV2) -> bool: ...

    def get_source(self, source_id: str) -> SourceRecordV2 | None: ...

    def put_snapshot(self, snapshot: SourceSnapshotV2) -> bool: ...

    def get_snapshot(self, snapshot_id: str) -> SourceSnapshotV2 | None: ...

    def put_document(self, document: DocumentRecord) -> bool: ...

    def put_evidence(self, evidence: EvidenceRefV2) -> bool: ...

    def get_evidence(self, evidence_id: str) -> EvidenceRefV2 | None: ...

    def put_project_card(self, card: ProjectCardV2) -> bool: ...

    def get_project_card(self, project_id: str) -> ProjectCardV2 | None: ...

    def put_component(self, component: ProjectComponentRecord) -> bool: ...

    def list_components(self, project_id: str) -> list[ProjectComponentRecord]: ...

    def put_wiki_page(self, page: WikiPageRecord) -> bool: ...

    def get_wiki_page(self, page_id: str) -> WikiPageRecord | None: ...

    def list_wiki_pages(self, project_id: str) -> list[WikiPageRecord]: ...

    def upsert_unit(self, unit: KnowledgeUnitV2) -> bool: ...

    def get_unit(self, knowledge_unit_id: str) -> KnowledgeUnitV2 | None: ...

    def iter_units(self) -> Iterator[KnowledgeUnitV2]: ...

    def put_relation(self, relation: RelationRecord) -> bool: ...

    def related(self, entity_id: str, *, limit: int = 20) -> list[RelationRecord]: ...

    def search(
        self,
        query: str,
        *,
        query_vectors: dict[str, list[float]] | None = None,
        filters: SearchFilters | None = None,
        limit: int = 10,
    ) -> list[SearchHit]: ...

    def put_reference_pack(self, pack: ReferencePackV2) -> bool: ...

    def get_reference_pack(self, reference_pack_id: str) -> ReferencePackV2 | None: ...

    def put_feedback(self, feedback: KnowledgeUsageFeedbackV1) -> bool: ...

    def get_feedback_governance(
        self, governance_id: str
    ) -> FeedbackGovernanceRecordV1 | None: ...

    def list_feedback_governance(
        self, *, queue: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[FeedbackGovernanceRecordV1]: ...

    def put_index_version(self, version: IndexVersionRecord) -> bool: ...

    def latest_index_version(self) -> IndexVersionRecord | None: ...

    def statistics(self) -> dict[str, Any]: ...

    def close(self) -> None: ...
