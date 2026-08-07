"""Deterministic in-memory implementation of the KnowStore contract.

This backend exists for unit tests and process-local dry runs. It is never
selected by the production factory unless explicitly requested.
"""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from typing import Any

from rosclaw_know.contracts import (
    EvidenceRefV2,
    FeedbackGovernanceRecordV1,
    KnowledgeClaimV1,
    KnowledgeUnitV2,
    KnowledgeUsageFeedbackV1,
    ProjectCardV2,
    ReferencePackV2,
    SourceDisagreementV1,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.feedback_governance import governance_for_feedback

from .base import ImmutableSnapshotError
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
from .ranking import cosine_similarity, exact_score, reciprocal_rank_fusion


def _wire_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unit_embedding_hash(unit: KnowledgeUnitV2) -> str:
    return _wire_hash(
        {
            "title": unit.title,
            "problem": unit.problem,
            "mechanism": unit.mechanism,
            "implementation": unit.implementation,
        }
    )


class InMemoryKnowStore:
    """Copy-on-write repository useful for deterministic contract tests."""

    def __init__(self) -> None:
        self.sources: dict[str, SourceRecordV2] = {}
        self.snapshots: dict[str, SourceSnapshotV2] = {}
        self.documents: dict[str, DocumentRecord] = {}
        self.evidence: dict[str, EvidenceRefV2] = {}
        self.project_cards: dict[str, ProjectCardV2] = {}
        self.components: dict[str, ProjectComponentRecord] = {}
        self.wiki_pages: dict[str, WikiPageRecord] = {}
        self.units: dict[str, KnowledgeUnitV2] = {}
        self.claims: dict[str, KnowledgeClaimV1] = {}
        self.source_disagreements: dict[str, SourceDisagreementV1] = {}
        self.relations: dict[str, RelationRecord] = {}
        self.reference_packs: dict[str, ReferencePackV2] = {}
        self.feedback: dict[str, KnowledgeUsageFeedbackV1] = {}
        self.feedback_governance: dict[str, FeedbackGovernanceRecordV1] = {}
        self.index_versions: dict[str, IndexVersionRecord] = {}
        self.embedding_hashes: dict[str, str] = {}
        self.embedding_writes = 0

    @property
    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            backend="memory",
            fulltext=True,
            hybrid_search=True,
            sql_join=False,
            ai_rerank=False,
            multi_vector=True,
            transactions="copy_on_write",
            degraded=["persistent_storage", "native_seekdb", "ai_rerank"],
            fulltext_analyzers=["deterministic_exact"],
            query_profiles=[
                "PROFILE_ERROR",
                "PROFILE_CODE",
                "PROFILE_CONCEPT",
                "PROFILE_PROJECT",
            ],
            native_hybrid_sql=False,
            rerank_unavailable_reason="memory backend has no SQL model service",
        )

    @contextmanager
    def transaction(self):
        state = copy.deepcopy(self.__dict__)
        try:
            yield
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(state)
            raise

    @staticmethod
    def _upsert(mapping: dict[str, Any], key: str, value: Any) -> bool:
        if mapping.get(key) == value:
            return False
        mapping[key] = copy.deepcopy(value)
        return True

    def upsert_source(self, source: SourceRecordV2) -> bool:
        return self._upsert(self.sources, source.source_id, source)

    def get_source(self, source_id: str) -> SourceRecordV2 | None:
        return copy.deepcopy(self.sources.get(source_id))

    def iter_sources(self):
        for source_id in sorted(self.sources):
            yield copy.deepcopy(self.sources[source_id])

    def put_snapshot(self, snapshot: SourceSnapshotV2) -> bool:
        existing = self.snapshots.get(snapshot.snapshot_id)
        if existing is not None:
            if existing != snapshot:
                raise ImmutableSnapshotError(f"snapshot {snapshot.snapshot_id!r} is immutable")
            return False
        identity = (snapshot.source_id, snapshot.version_kind, snapshot.version_value)
        for other in self.snapshots.values():
            if (other.source_id, other.version_kind, other.version_value) == identity:
                if other != snapshot:
                    raise ImmutableSnapshotError(
                        "snapshot version identity already has different immutable content"
                    )
                return False
        self.snapshots[snapshot.snapshot_id] = copy.deepcopy(snapshot)
        return True

    def get_snapshot(self, snapshot_id: str) -> SourceSnapshotV2 | None:
        value = self.snapshots.get(snapshot_id)
        return copy.deepcopy(value)

    def iter_snapshots(self):
        for snapshot_id in sorted(self.snapshots):
            yield copy.deepcopy(self.snapshots[snapshot_id])

    def put_document(self, document: DocumentRecord) -> bool:
        if document.snapshot_id not in self.snapshots:
            raise ValueError(f"unknown snapshot: {document.snapshot_id}")
        existing = self.documents.get(document.document_id)
        if existing is not None and existing != document:
            raise ImmutableSnapshotError(
                f"document {document.document_id!r} belongs to an immutable snapshot"
            )
        return self._upsert(self.documents, document.document_id, document)

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return copy.deepcopy(self.documents.get(document_id))

    def list_documents(self, snapshot_id: str) -> list[DocumentRecord]:
        return copy.deepcopy(
            sorted(
                (item for item in self.documents.values() if item.snapshot_id == snapshot_id),
                key=lambda item: (item.path, item.document_id),
            )
        )

    def put_evidence(self, evidence: EvidenceRefV2) -> bool:
        if evidence.snapshot_id not in self.snapshots:
            raise ValueError(f"unknown snapshot: {evidence.snapshot_id}")
        if evidence.document_id not in self.documents:
            raise ValueError(f"unknown document: {evidence.document_id}")
        existing = self.evidence.get(evidence.evidence_id)
        if existing is not None and existing != evidence:
            raise ImmutableSnapshotError(
                f"evidence {evidence.evidence_id!r} belongs to an immutable snapshot"
            )
        return self._upsert(self.evidence, evidence.evidence_id, evidence)

    def get_evidence(self, evidence_id: str) -> EvidenceRefV2 | None:
        return copy.deepcopy(self.evidence.get(evidence_id))

    def put_project_card(self, card: ProjectCardV2) -> bool:
        if card.source_snapshot_id not in self.snapshots:
            raise ValueError(f"unknown snapshot: {card.source_snapshot_id}")
        return self._upsert(self.project_cards, card.project_id, card)

    def get_project_card(self, project_id: str) -> ProjectCardV2 | None:
        return copy.deepcopy(self.project_cards.get(project_id))

    def put_component(self, component: ProjectComponentRecord) -> bool:
        if component.snapshot_id not in self.snapshots:
            raise ValueError(f"unknown snapshot: {component.snapshot_id}")
        return self._upsert(self.components, component.component_id, component)

    def list_components(self, project_id: str) -> list[ProjectComponentRecord]:
        return copy.deepcopy(
            sorted(
                (item for item in self.components.values() if item.project_id == project_id),
                key=lambda item: item.path,
            )
        )

    def put_wiki_page(self, page: WikiPageRecord) -> bool:
        if page.snapshot_id not in self.snapshots:
            raise ValueError(f"unknown snapshot: {page.snapshot_id}")
        return self._upsert(self.wiki_pages, page.page_id, page)

    def get_wiki_page(self, page_id: str) -> WikiPageRecord | None:
        return copy.deepcopy(self.wiki_pages.get(page_id))

    def list_wiki_pages(self, project_id: str) -> list[WikiPageRecord]:
        return copy.deepcopy(
            sorted(
                (item for item in self.wiki_pages.values() if item.project_id == project_id),
                key=lambda item: (item.outline_order, item.page_id),
            )
        )

    def upsert_unit(self, unit: KnowledgeUnitV2) -> bool:
        for snapshot_id in unit.source_snapshot_ids:
            if snapshot_id not in self.snapshots:
                raise ValueError(f"unknown snapshot: {snapshot_id}")
        for item in unit.evidence_refs:
            if item.evidence_id not in self.evidence:
                raise ValueError(f"unknown evidence: {item.evidence_id}")
        existing = self.units.get(unit.knowledge_unit_id)
        if existing == unit:
            return False

        embedding_hash = _unit_embedding_hash(unit)
        if self.embedding_hashes.get(unit.knowledge_unit_id) == embedding_hash and existing:
            unit = unit.model_copy(update={"vectors": existing.vectors}, deep=True)
        else:
            self.embedding_hashes[unit.knowledge_unit_id] = embedding_hash
            if any(
                vector is not None
                for vector in (
                    unit.vectors.problem,
                    unit.vectors.mechanism,
                    unit.vectors.content,
                    unit.vectors.code,
                )
            ):
                self.embedding_writes += 1
        self.units[unit.knowledge_unit_id] = copy.deepcopy(unit)
        return True

    def get_unit(self, knowledge_unit_id: str) -> KnowledgeUnitV2 | None:
        return copy.deepcopy(self.units.get(knowledge_unit_id))

    def iter_units(self):
        for unit_id in sorted(self.units):
            yield copy.deepcopy(self.units[unit_id])

    def put_claim(self, claim: KnowledgeClaimV1) -> bool:
        for snapshot_id in claim.source_snapshot_ids:
            if snapshot_id not in self.snapshots:
                raise ValueError(f"unknown snapshot: {snapshot_id}")
        for evidence in claim.evidence_refs:
            if evidence.evidence_id not in self.evidence:
                raise ValueError(f"unknown evidence: {evidence.evidence_id}")
        if claim.knowledge_unit_id and claim.knowledge_unit_id not in self.units:
            raise ValueError(f"unknown knowledge unit: {claim.knowledge_unit_id}")
        return self._upsert(self.claims, claim.claim_id, claim)

    def get_claim(self, claim_id: str) -> KnowledgeClaimV1 | None:
        return copy.deepcopy(self.claims.get(claim_id))

    def list_claims(
        self,
        *,
        knowledge_unit_id: str | None = None,
        snapshot_id: str | None = None,
        status: str | None = None,
    ) -> list[KnowledgeClaimV1]:
        claims = [
            item
            for item in self.claims.values()
            if (knowledge_unit_id is None or item.knowledge_unit_id == knowledge_unit_id)
            and (snapshot_id is None or snapshot_id in item.source_snapshot_ids)
            and (status is None or item.status == status)
        ]
        claims.sort(key=lambda item: (item.subject, item.predicate, item.claim_id))
        return copy.deepcopy(claims)

    def put_source_disagreement(self, disagreement: SourceDisagreementV1) -> bool:
        for claim_id in disagreement.claim_ids:
            if claim_id not in self.claims:
                raise ValueError(f"unknown claim: {claim_id}")
        return self._upsert(
            self.source_disagreements, disagreement.disagreement_id, disagreement
        )

    def get_source_disagreement(self, disagreement_id: str) -> SourceDisagreementV1 | None:
        return copy.deepcopy(self.source_disagreements.get(disagreement_id))

    def list_source_disagreements(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[SourceDisagreementV1]:
        if limit <= 0:
            return []
        records = [
            item
            for item in self.source_disagreements.values()
            if status is None or item.status == status
        ]
        records.sort(key=lambda item: (item.updated_at, item.disagreement_id), reverse=True)
        return copy.deepcopy(records[:limit])

    def put_relation(self, relation: RelationRecord) -> bool:
        if relation.evidence_id not in self.evidence:
            raise ValueError(f"unknown evidence: {relation.evidence_id}")
        return self._upsert(self.relations, relation.relation_id, relation)

    def related(self, entity_id: str, *, limit: int = 20) -> list[RelationRecord]:
        values = [
            relation
            for relation in self.relations.values()
            if relation.from_id == entity_id or relation.to_id == entity_id
        ]
        values.sort(key=lambda relation: (-relation.confidence, relation.relation_id))
        return copy.deepcopy(values[:limit])

    @staticmethod
    def _matches_filters(unit: KnowledgeUnitV2, filters: SearchFilters) -> bool:
        if filters.unit_types and unit.unit_type not in filters.unit_types:
            return False
        if filters.snapshot_ids and not set(filters.snapshot_ids) & set(unit.source_snapshot_ids):
            return False
        if filters.status and unit.status not in filters.status:
            return False
        if filters.robot and unit.robot_constraints and filters.robot not in unit.robot_constraints:
            return False
        if (
            filters.ros_distro
            and unit.software_constraints.get("ros")
            and unit.software_constraints["ros"] != filters.ros_distro
        ):
            return False
        if (
            filters.simulator
            and unit.software_constraints.get("simulator")
            and unit.software_constraints["simulator"] != filters.simulator
        ):
            return False
        return True

    def search(
        self,
        query: str,
        *,
        query_vectors: dict[str, list[float]] | None = None,
        filters: SearchFilters | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        filters = filters or SearchFilters()
        units = [unit for unit in self.iter_units() if self._matches_filters(unit, filters)]
        if not units or limit <= 0:
            return []

        exact: dict[str, float] = {}
        vector_scores: dict[str, dict[str, float]] = {}
        rankings: list[list[str]] = []
        for unit in units:
            text = "\n".join((unit.title, unit.problem, unit.mechanism, unit.implementation))
            exact[unit.knowledge_unit_id] = exact_score(query, text)
        rankings.append(sorted(exact, key=lambda item: (-exact[item], item)))

        for field, query_vector in sorted((query_vectors or {}).items()):
            if field not in {"problem", "mechanism", "content", "code"}:
                continue
            field_scores: dict[str, float] = {}
            for unit in units:
                vector = getattr(unit.vectors, field)
                if vector is not None:
                    field_scores[unit.knowledge_unit_id] = cosine_similarity(query_vector, vector)
            vector_scores[field] = field_scores
            rankings.append(sorted(field_scores, key=lambda item: (-field_scores[item], item)))

        rrf = reciprocal_rank_fusion(rankings)
        hits: list[SearchHit] = []
        for unit in units:
            unit_id = unit.knowledge_unit_id
            breakdown = {"exact": exact[unit_id], "rrf": rrf.get(unit_id, 0.0)}
            matched_by = ["fulltext"] if exact[unit_id] > 0 else []
            for field, scores in vector_scores.items():
                if unit_id in scores:
                    breakdown[f"vector_{field}"] = scores[unit_id]
                    matched_by.append(f"vector_{field}")
            score = exact[unit_id] * 0.7 + rrf.get(unit_id, 0.0) * 0.3
            hits.append(
                SearchHit(
                    knowledge_unit_id=unit_id,
                    score=score,
                    score_breakdown=breakdown,
                    matched_by=matched_by,
                    warnings=["AI_RERANK unavailable; deterministic RRF fallback used"],
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.knowledge_unit_id))
        return hits[:limit]

    def put_reference_pack(self, pack: ReferencePackV2) -> bool:
        return self._upsert(self.reference_packs, pack.reference_pack_id, pack)

    def get_reference_pack(self, reference_pack_id: str) -> ReferencePackV2 | None:
        return copy.deepcopy(self.reference_packs.get(reference_pack_id))

    def iter_reference_packs(self):
        for pack_id in sorted(self.reference_packs):
            yield copy.deepcopy(self.reference_packs[pack_id])

    def put_feedback(self, feedback: KnowledgeUsageFeedbackV1) -> bool:
        existing = self.feedback.get(feedback.feedback_id)
        if existing is not None and existing != feedback:
            raise ValueError(f"feedback ID conflict: {feedback.feedback_id}")
        created = self._upsert(self.feedback, feedback.feedback_id, feedback)
        governance = governance_for_feedback(feedback)
        self._upsert(self.feedback_governance, governance.governance_id, governance)
        return created

    def get_feedback_governance(
        self, governance_id: str
    ) -> FeedbackGovernanceRecordV1 | None:
        return copy.deepcopy(self.feedback_governance.get(governance_id))

    def list_feedback_governance(
        self, *, queue: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[FeedbackGovernanceRecordV1]:
        if limit <= 0:
            return []
        records = [
            item
            for item in self.feedback_governance.values()
            if (queue is None or item.queue == queue) and (status is None or item.status == status)
        ]
        records.sort(key=lambda item: (item.created_at, item.governance_id), reverse=True)
        return copy.deepcopy(records[:limit])

    def review_feedback_governance(
        self, governance_id: str, *, decision: str
    ) -> FeedbackGovernanceRecordV1 | None:
        if decision not in {"apply", "reject"}:
            raise ValueError("decision must be 'apply' or 'reject'")
        record = self.feedback_governance.get(governance_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={"status": "reviewed" if decision == "apply" else "dismissed"}
        )
        self.feedback_governance[governance_id] = updated
        return copy.deepcopy(updated)

    def put_index_version(self, version: IndexVersionRecord) -> bool:
        existing = self.index_versions.get(version.index_version)
        if existing is not None and existing != version:
            raise ValueError(f"index version conflict: {version.index_version}")
        return self._upsert(self.index_versions, version.index_version, version)

    def latest_index_version(self) -> IndexVersionRecord | None:
        if not self.index_versions:
            return None
        return copy.deepcopy(max(self.index_versions.values(), key=lambda item: item.created_at))

    def statistics(self) -> dict[str, Any]:
        return {
            "source_count": len(self.sources),
            "snapshot_count": len(self.snapshots),
            "project_count": len(self.project_cards),
            "wiki_page_count": len(self.wiki_pages),
            "knowledge_unit_count": len(self.units),
            "claim_count": len(self.claims),
            "source_disagreement_count": len(self.source_disagreements),
            "reference_pack_count": len(self.reference_packs),
            "feedback_count": len(self.feedback),
            "feedback_governance_count": len(self.feedback_governance),
        }

    def close(self) -> None:
        return None
