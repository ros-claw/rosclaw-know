"""Canonical pyseekdb collection backend for Know v2.

Logical relational records are stored as versioned JSON documents in named
SeekDB collections. The four unit vectors live in coordinated collections
keyed by the same knowledge-unit ID. The base unit record is committed last,
so an interrupted multi-vector write is not visible to readers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
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
from rosclaw_know.source_authority import source_authority

from .base import ImmutableSnapshotError, StoreConfigurationError
from .isolation import guard_store_isolation
from .memory import InMemoryKnowStore, _unit_embedding_hash
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
from .ranking import exact_score, reciprocal_rank_fusion
from .server_native import NativeHybridDocument, NativeHybridQueryEngine, NativeHybridTrace

COLLECTIONS = {
    "source": "know_source_v2",
    "snapshot": "know_source_snapshot_v2",
    "document": "know_document_v2",
    "evidence": "know_evidence_v2",
    "project_card": "know_project_card_v2",
    "component": "know_project_component_v2",
    "wiki_page": "know_wiki_page_v2",
    "unit": "know_unit_v2",
    "claim": "know_claim_v1",
    "relation": "know_relation_v2",
    "reference_pack": "know_reference_pack_v2",
    "feedback": "know_usage_feedback_v1",
    "feedback_governance": "know_feedback_governance_v1",
    "source_disagreement": "know_source_disagreement_v1",
    "index_version": "know_index_version_v2",
}
VECTOR_FIELDS = ("problem", "mechanism", "content", "code")
FULLTEXT_ANALYZERS = ("ngram", "beng", "ik")


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse(model: Any, payload: dict[str, Any]) -> Any:
    """Validate a decoded record with JSON-origin strict semantics."""

    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def _already_exists(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return "already exists" in message or "1007" in message


class SeekDBKnowStore:
    """SeekDB embedded/server implementation of the repository protocol."""

    def __init__(
        self,
        *,
        mode: str = "embedded",
        database: str = "rosclaw_know",
        path: str | Path | None = None,
        host: str | None = None,
        port: int = 2881,
        tenant: str = "sys",
        user: str = "root",
        password: str = "",
        memory_database: str | None = None,
        practice_database: str | None = None,
        memory_path: str | Path | None = None,
        practice_path: str | Path | None = None,
    ) -> None:
        if mode not in {"embedded", "server"}:
            raise StoreConfigurationError(f"unsupported SeekDB mode: {mode!r}")
        if mode == "embedded" and path is None:
            raise StoreConfigurationError("embedded SeekDB requires an explicit path")
        if mode == "server" and not host:
            raise StoreConfigurationError("server SeekDB requires a host")
        guard_store_isolation(
            know_database=database,
            memory_database=memory_database,
            practice_database=practice_database,
            know_path=path,
            memory_path=memory_path,
            practice_path=practice_path,
        )

        try:
            import pyseekdb
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise StoreConfigurationError(
                "pyseekdb is required; install rosclaw-know[seekdb]"
            ) from exc

        self._mode = mode
        self._database = database
        common: dict[str, Any] = {"tenant": tenant}
        if mode == "embedded":
            seekdb_path = str(Path(path).expanduser().resolve(strict=False))
            Path(seekdb_path).mkdir(parents=True, exist_ok=True)
            admin = pyseekdb.AdminClient(path=seekdb_path, **common)
            client_args: dict[str, Any] = {"path": seekdb_path}
        else:
            common.update(host=host, port=port, user=user, password=password)
            admin = pyseekdb.AdminClient(**common)
            client_args = dict(common)
        try:
            admin.create_database(database, tenant=tenant)
        except Exception as exc:  # noqa: BLE001
            if not _already_exists(exc):
                # Some server roles cannot CREATE but may access an existing DB.
                try:
                    names = {item.name for item in admin.list_databases(tenant=tenant)}
                except Exception:  # noqa: BLE001
                    names = set()
                if database not in names:
                    raise StoreConfigurationError(
                        f"cannot create or verify SeekDB database {database!r}: {exc}"
                    ) from exc
        self._client = pyseekdb.Client(database=database, **client_args)
        self._collections: dict[str, Any] = {}
        for logical, name in COLLECTIONS.items():
            self._collections[logical] = self._client.get_or_create_collection(
                name,
                configuration=pyseekdb.HNSWConfiguration(dimension=1, distance="cosine"),
                embedding_function=None,
            )
        self._vector_collections: dict[tuple[str, int], Any] = {}
        self._fulltext_collections: dict[str, Any] = {}
        self._degraded_features: set[str] = {"ai_rerank"}
        self._native_connection: Any | None = None
        self._native_engines: dict[int, NativeHybridQueryEngine] = {}
        self._last_native_hybrid_trace: NativeHybridTrace | None = None
        self._rerank_unavailable_reason = "AI_RERANK model service is not configured"
        if mode == "server":
            try:
                import pymysql

                self._native_connection = pymysql.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                    autocommit=False,
                )
            except Exception as exc:  # noqa: BLE001 - explicit capability degradation
                self._degraded_features.add("native_hybrid_sql")
                self._rerank_unavailable_reason = (
                    f"native SQL unavailable: {type(exc).__name__}"
                )
        for analyzer in FULLTEXT_ANALYZERS:
            try:
                schema = pyseekdb.Schema(
                    vector_index=pyseekdb.VectorIndexConfig(
                        hnsw=pyseekdb.HNSWConfiguration(dimension=1, distance="cosine"),
                        embedding_function=None,
                    ),
                    fulltext_index=pyseekdb.FulltextIndexConfig(analyzer=analyzer),
                )
                self._fulltext_collections[analyzer] = self._client.get_or_create_collection(
                    f"know_unit_fulltext_{analyzer}_v2",
                    schema=schema,
                )
            except Exception:  # noqa: BLE001 - capability is reported explicitly
                self._degraded_features.add(f"fulltext_{analyzer}")

    @property
    def capabilities(self) -> StoreCapabilities:
        server = self._mode == "server"
        degraded = sorted(self._degraded_features)
        if not server:
            degraded.extend(["sql_join", "cross_collection_transaction"])
        return StoreCapabilities(
            backend="seekdb_server" if server else "seekdb_embedded",
            fulltext=bool(self._fulltext_collections),
            hybrid_search=bool(self._fulltext_collections),
            sql_join=server,
            ai_rerank=False,
            multi_vector=True,
            transactions="native" if server else "single_record",
            degraded=list(dict.fromkeys(degraded)),
            fulltext_analyzers=sorted(self._fulltext_collections),
            query_profiles=[
                "PROFILE_ERROR",
                "PROFILE_CODE",
                "PROFILE_CONCEPT",
                "PROFILE_PROJECT",
            ],
            native_hybrid_sql=server and self._native_connection is not None,
            rerank_unavailable_reason=(
                self._rerank_unavailable_reason
                if "ai_rerank" in self._degraded_features
                else None
            ),
        )

    @contextmanager
    def transaction(self):
        """Expose the backend transaction boundary.

        Collection operations are atomic per record. Unit multi-vector writes
        use a commit-last base record. Server SQL migrations use native SQL
        transactions outside this collection repository.
        """

        yield

    def _get_payload(self, logical: str, record_id: str) -> dict[str, Any] | None:
        result = self._collections[logical].get(ids=record_id, include=["documents"])
        if not result.get("ids"):
            return None
        documents = result.get("documents") or []
        return json.loads(documents[0]) if documents else None

    def _iter_payloads(self, logical: str, *, limit: int = 100_000):
        result = self._collections[logical].get(limit=limit, include=["documents"])
        for document in result.get("documents") or []:
            yield json.loads(document)

    def _put_payload(
        self,
        logical: str,
        record_id: str,
        value: Any,
        *,
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> bool:
        document = _json(value)
        existing = self._get_payload(logical, record_id)
        decoded = json.loads(document)
        if existing == decoded:
            return False
        self._collections[logical].upsert(
            ids=record_id,
            # pyseekdb 1.4 requires every collection to declare a vector
            # dimension. Logical JSON tables therefore use a one-dimensional
            # sentinel; business embeddings never use this field.
            embeddings=[0.0],
            documents=document,
            metadatas={
                "record_hash": hashlib.sha256(document.encode()).hexdigest(),
                **(metadata or {}),
            },
        )
        return True

    def upsert_source(self, source: SourceRecordV2) -> bool:
        return self._put_payload(
            "source", source.source_id, source, metadata={"source_type": source.source_type}
        )

    def get_source(self, source_id: str) -> SourceRecordV2 | None:
        payload = self._get_payload("source", source_id)
        return _parse(SourceRecordV2, payload) if payload else None

    def iter_sources(self):
        sources = [_parse(SourceRecordV2, value) for value in self._iter_payloads("source")]
        yield from sorted(sources, key=lambda item: item.source_id)

    def put_snapshot(self, snapshot: SourceSnapshotV2) -> bool:
        existing = self._get_payload("snapshot", snapshot.snapshot_id)
        payload = snapshot.model_dump(mode="json", exclude_none=False)
        if existing is not None:
            if existing != payload:
                raise ImmutableSnapshotError(f"snapshot {snapshot.snapshot_id!r} is immutable")
            return False
        identity = (snapshot.source_id, snapshot.version_kind, snapshot.version_value)
        for other_payload in self._iter_payloads("snapshot"):
            other = _parse(SourceSnapshotV2, other_payload)
            if (other.source_id, other.version_kind, other.version_value) == identity:
                if other != snapshot:
                    raise ImmutableSnapshotError(
                        "snapshot version identity already has different immutable content"
                    )
                return False
        return self._put_payload(
            "snapshot",
            snapshot.snapshot_id,
            snapshot,
            metadata={
                "source_id": snapshot.source_id,
                "version_kind": snapshot.version_kind,
                "version_value": snapshot.version_value,
            },
        )

    def get_snapshot(self, snapshot_id: str) -> SourceSnapshotV2 | None:
        payload = self._get_payload("snapshot", snapshot_id)
        return _parse(SourceSnapshotV2, payload) if payload else None

    def iter_snapshots(self):
        snapshots = [
            _parse(SourceSnapshotV2, value) for value in self._iter_payloads("snapshot")
        ]
        yield from sorted(snapshots, key=lambda item: item.snapshot_id)

    def put_document(self, document: DocumentRecord) -> bool:
        if self.get_snapshot(document.snapshot_id) is None:
            raise ValueError(f"unknown snapshot: {document.snapshot_id}")
        existing = self._get_payload("document", document.document_id)
        payload = document.model_dump(mode="json", exclude_none=False)
        if existing is not None and existing != payload:
            raise ImmutableSnapshotError(
                f"document {document.document_id!r} belongs to an immutable snapshot"
            )
        return self._put_payload(
            "document",
            document.document_id,
            document,
            metadata={"snapshot_id": document.snapshot_id},
        )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        payload = self._get_payload("document", document_id)
        return _parse(DocumentRecord, payload) if payload else None

    def list_documents(self, snapshot_id: str) -> list[DocumentRecord]:
        return sorted(
            (
                _parse(DocumentRecord, value)
                for value in self._iter_payloads("document")
                if value["snapshot_id"] == snapshot_id
            ),
            key=lambda item: (item.path, item.document_id),
        )

    def put_evidence(self, evidence: EvidenceRefV2) -> bool:
        if self.get_snapshot(evidence.snapshot_id) is None:
            raise ValueError(f"unknown snapshot: {evidence.snapshot_id}")
        if self._get_payload("document", evidence.document_id) is None:
            raise ValueError(f"unknown document: {evidence.document_id}")
        existing = self._get_payload("evidence", evidence.evidence_id)
        payload = evidence.model_dump(mode="json", exclude_none=False)
        if existing is not None and existing != payload:
            raise ImmutableSnapshotError(
                f"evidence {evidence.evidence_id!r} belongs to an immutable snapshot"
            )
        return self._put_payload(
            "evidence",
            evidence.evidence_id,
            evidence,
            metadata={"snapshot_id": evidence.snapshot_id},
        )

    def get_evidence(self, evidence_id: str) -> EvidenceRefV2 | None:
        payload = self._get_payload("evidence", evidence_id)
        return _parse(EvidenceRefV2, payload) if payload else None

    def put_project_card(self, card: ProjectCardV2) -> bool:
        if self.get_snapshot(card.source_snapshot_id) is None:
            raise ValueError(f"unknown snapshot: {card.source_snapshot_id}")
        return self._put_payload(
            "project_card",
            card.project_id,
            card,
            metadata={"snapshot_id": card.source_snapshot_id},
        )

    def get_project_card(self, project_id: str) -> ProjectCardV2 | None:
        payload = self._get_payload("project_card", project_id)
        return _parse(ProjectCardV2, payload) if payload else None

    def put_component(self, component: ProjectComponentRecord) -> bool:
        if self.get_snapshot(component.snapshot_id) is None:
            raise ValueError(f"unknown snapshot: {component.snapshot_id}")
        return self._put_payload(
            "component",
            component.component_id,
            component,
            metadata={"project_id": component.project_id, "snapshot_id": component.snapshot_id},
        )

    def list_components(self, project_id: str) -> list[ProjectComponentRecord]:
        return sorted(
            (
                _parse(ProjectComponentRecord, value)
                for value in self._iter_payloads("component")
                if value["project_id"] == project_id
            ),
            key=lambda item: item.path,
        )

    def put_wiki_page(self, page: WikiPageRecord) -> bool:
        if self.get_snapshot(page.snapshot_id) is None:
            raise ValueError(f"unknown snapshot: {page.snapshot_id}")
        return self._put_payload(
            "wiki_page",
            page.page_id,
            page,
            metadata={"project_id": page.project_id, "snapshot_id": page.snapshot_id},
        )

    def get_wiki_page(self, page_id: str) -> WikiPageRecord | None:
        payload = self._get_payload("wiki_page", page_id)
        return _parse(WikiPageRecord, payload) if payload else None

    def list_wiki_pages(self, project_id: str) -> list[WikiPageRecord]:
        return sorted(
            (
                _parse(WikiPageRecord, value)
                for value in self._iter_payloads("wiki_page")
                if value["project_id"] == project_id
            ),
            key=lambda item: (item.outline_order, item.page_id),
        )

    def _vector_collection(self, field: str, dimension: int):
        key = (field, dimension)
        if key not in self._vector_collections:
            import pyseekdb

            name = f"know_unit_{field}_vector_d{dimension}"
            self._vector_collections[key] = self._client.get_or_create_collection(
                name,
                configuration=pyseekdb.HNSWConfiguration(dimension=dimension, distance="cosine"),
                embedding_function=None,
            )
        return self._vector_collections[key]

    def _native_engine(self, dimension: int) -> NativeHybridQueryEngine:
        if self._native_connection is None:
            raise StoreConfigurationError("native SeekDB SQL connection is unavailable")
        if dimension not in self._native_engines:
            engine = NativeHybridQueryEngine(
                self._native_connection,
                table=f"know_unit_hybrid_d{dimension}",
                dimension=dimension,
            )
            engine.ensure_schema()
            self._native_engines[dimension] = engine
            model_key = os.environ.get("ROSCLAW_KNOW_RERANK_MODEL_KEY")
            capability = engine.rerank_capability(model_key)
            if capability["available"]:
                self._degraded_features.discard("ai_rerank")
            else:
                self._rerank_unavailable_reason = str(capability["reason"])
        return self._native_engines[dimension]

    @staticmethod
    def _surface_tokens(unit: KnowledgeUnitV2) -> tuple[str, str]:
        text = "\n".join((unit.title, unit.problem, unit.mechanism, unit.implementation))
        symbols = re.findall(
            r"\b(?:[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+|"
            r"[A-Za-z_][A-Za-z0-9_]{3,}|[A-Z][A-Z0-9_]{2,})\b",
            text,
        )
        api = [item for item in symbols if "::" in item or "_" in item]
        return " ".join(dict.fromkeys(symbols)), " ".join(dict.fromkeys(api))

    def upsert_unit(self, unit: KnowledgeUnitV2) -> bool:
        for snapshot_id in unit.source_snapshot_ids:
            if self.get_snapshot(snapshot_id) is None:
                raise ValueError(f"unknown snapshot: {snapshot_id}")
        for item in unit.evidence_refs:
            if self._get_payload("evidence", item.evidence_id) is None:
                raise ValueError(f"unknown evidence: {item.evidence_id}")
        existing_payload = self._get_payload("unit", unit.knowledge_unit_id)
        existing = _parse(KnowledgeUnitV2, existing_payload) if existing_payload else None
        embedding_hash = _unit_embedding_hash(unit)
        if existing is not None and _unit_embedding_hash(existing) == embedding_hash:
            unit = unit.model_copy(update={"vectors": existing.vectors}, deep=True)
        else:
            for field in VECTOR_FIELDS:
                vector = getattr(unit.vectors, field)
                if vector is None:
                    continue
                text = getattr(unit, field, None) or unit.implementation
                collection = self._vector_collection(field, len(vector))
                collection.upsert(
                    ids=unit.knowledge_unit_id,
                    embeddings=vector,
                    documents=text,
                    metadatas={"field": field, "dimension": len(vector)},
                )
                # SeekDB vector indexes may build asynchronously. Refresh at
                # the unit commit boundary so an immediately-issued query is
                # read-after-write consistent.
                collection.refresh_index()
            retrieval_text = "\n".join(
                (unit.title, unit.problem, unit.mechanism, unit.implementation)
            )
            for analyzer, collection in self._fulltext_collections.items():
                try:
                    collection.upsert(
                        ids=unit.knowledge_unit_id,
                        embeddings=[0.0],
                        documents=retrieval_text,
                        metadatas={"analyzer": analyzer},
                    )
                    collection.refresh_index()
                except Exception:  # noqa: BLE001 - base record remains usable
                    self._degraded_features.add(f"fulltext_{analyzer}")
            native_vector = next(
                (
                    vector
                    for vector in (
                        unit.vectors.content,
                        unit.vectors.problem,
                        unit.vectors.code,
                        unit.vectors.mechanism,
                    )
                    if vector is not None
                ),
                None,
            )
            if native_vector is not None and self._native_connection is not None:
                try:
                    symbols, api = self._surface_tokens(unit)
                    authority = "D"
                    if unit.evidence_refs:
                        source = self.get_source(unit.evidence_refs[0].source_id)
                        if source is not None:
                            authority = source_authority(source)[0]
                    self._native_engine(len(native_vector)).put(
                        NativeHybridDocument(
                            record_id=unit.knowledge_unit_id,
                            content="\n".join(
                                (unit.title, unit.problem, unit.mechanism, unit.implementation)
                            ),
                            zh_content="\n".join(
                                value
                                for value in (unit.title, unit.problem, unit.mechanism)
                                if re.search(r"[\u3400-\u9fff]", value)
                            ),
                            error_surface="\n".join(
                                (unit.title, unit.problem, *unit.contraindications)
                            ),
                            symbol_surface=symbols,
                            path_surface=" ".join(
                                dict.fromkeys(item.path for item in unit.evidence_refs)
                            ),
                            api_surface=api,
                            source_authority=authority,  # type: ignore[arg-type]
                            compatibility_status="unknown",
                            status=unit.status,
                            unit_type=unit.unit_type,
                            embedding=native_vector,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - collection path remains available
                    self._degraded_features.add("native_hybrid_sql")
                    self._rerank_unavailable_reason = (
                        f"native hybrid write degraded: {type(exc).__name__}"
                    )
        return self._put_payload(
            "unit",
            unit.knowledge_unit_id,
            unit,
            metadata={
                "unit_type": unit.unit_type,
                "status": unit.status,
                "embedding_hash": embedding_hash,
            },
        )

    def get_unit(self, knowledge_unit_id: str) -> KnowledgeUnitV2 | None:
        payload = self._get_payload("unit", knowledge_unit_id)
        return _parse(KnowledgeUnitV2, payload) if payload else None

    def iter_units(self):
        units = [_parse(KnowledgeUnitV2, value) for value in self._iter_payloads("unit")]
        yield from sorted(units, key=lambda unit: unit.knowledge_unit_id)

    def put_claim(self, claim: KnowledgeClaimV1) -> bool:
        for snapshot_id in claim.source_snapshot_ids:
            if self.get_snapshot(snapshot_id) is None:
                raise ValueError(f"unknown snapshot: {snapshot_id}")
        for evidence in claim.evidence_refs:
            if self._get_payload("evidence", evidence.evidence_id) is None:
                raise ValueError(f"unknown evidence: {evidence.evidence_id}")
        if claim.knowledge_unit_id and self.get_unit(claim.knowledge_unit_id) is None:
            raise ValueError(f"unknown knowledge unit: {claim.knowledge_unit_id}")
        return self._put_payload(
            "claim",
            claim.claim_id,
            claim,
            metadata={
                "knowledge_unit_id": claim.knowledge_unit_id or "",
                "status": claim.status,
                "claim_type": claim.claim_type,
            },
        )

    def get_claim(self, claim_id: str) -> KnowledgeClaimV1 | None:
        payload = self._get_payload("claim", claim_id)
        return _parse(KnowledgeClaimV1, payload) if payload else None

    def list_claims(
        self,
        *,
        knowledge_unit_id: str | None = None,
        snapshot_id: str | None = None,
        status: str | None = None,
    ) -> list[KnowledgeClaimV1]:
        claims = [_parse(KnowledgeClaimV1, value) for value in self._iter_payloads("claim")]
        claims = [
            item
            for item in claims
            if (knowledge_unit_id is None or item.knowledge_unit_id == knowledge_unit_id)
            and (snapshot_id is None or snapshot_id in item.source_snapshot_ids)
            and (status is None or item.status == status)
        ]
        return sorted(claims, key=lambda item: (item.subject, item.predicate, item.claim_id))

    def put_source_disagreement(self, disagreement: SourceDisagreementV1) -> bool:
        for claim_id in disagreement.claim_ids:
            if self.get_claim(claim_id) is None:
                raise ValueError(f"unknown claim: {claim_id}")
        return self._put_payload(
            "source_disagreement",
            disagreement.disagreement_id,
            disagreement,
            metadata={"status": disagreement.status},
        )

    def get_source_disagreement(self, disagreement_id: str) -> SourceDisagreementV1 | None:
        payload = self._get_payload("source_disagreement", disagreement_id)
        return _parse(SourceDisagreementV1, payload) if payload else None

    def list_source_disagreements(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[SourceDisagreementV1]:
        if limit <= 0:
            return []
        records = [
            _parse(SourceDisagreementV1, value)
            for value in self._iter_payloads("source_disagreement")
        ]
        records = [item for item in records if status is None or item.status == status]
        records.sort(key=lambda item: (item.updated_at, item.disagreement_id), reverse=True)
        return records[:limit]

    def put_relation(self, relation: RelationRecord) -> bool:
        if self._get_payload("evidence", relation.evidence_id) is None:
            raise ValueError(f"unknown evidence: {relation.evidence_id}")
        return self._put_payload(
            "relation",
            relation.relation_id,
            relation,
            metadata={"from_id": relation.from_id, "to_id": relation.to_id},
        )

    def related(self, entity_id: str, *, limit: int = 20) -> list[RelationRecord]:
        relations = [
            _parse(RelationRecord, value)
            for value in self._iter_payloads("relation")
            if value["from_id"] == entity_id or value["to_id"] == entity_id
        ]
        return sorted(relations, key=lambda item: (-item.confidence, item.relation_id))[:limit]

    @staticmethod
    def _matches_filters(unit: KnowledgeUnitV2, filters: SearchFilters) -> bool:
        return InMemoryKnowStore._matches_filters(unit, filters)

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
        by_id = {unit.knowledge_unit_id: unit for unit in units}
        if not by_id:
            return []

        if self._native_connection is not None and query_vectors:
            for field in ("content", "problem", "code", "mechanism"):
                vector = query_vectors.get(field)
                if not vector:
                    continue
                try:
                    folded = query.casefold()
                    profile = (
                        "PROFILE_ERROR"
                        if re.search(r"\b(error|exception|failed|timeout)\b|(?<!\w)-\d+", folded)
                        else "PROFILE_CODE"
                        if re.search(r"\b(file|module|class|symbol|config|entrypoint|where)\b|[/\\]", folded)
                        else "PROFILE_PROJECT"
                        if re.search(r"\b(project|repository|repo|paper)\b|项目|论文", folded)
                        else "PROFILE_CONCEPT"
                    )
                    native_filters = (
                        {"status": filters.status[0]} if len(filters.status) == 1 else {}
                    )
                    trace = self._native_engine(len(vector)).query(
                        query=query,
                        embedding=vector,
                        profile=profile,  # type: ignore[arg-type]
                        filters=native_filters,
                        limit=limit,
                    )
                    self._last_native_hybrid_trace = trace
                    hits = []
                    for result in trace.results:
                        unit_id = str(result.get("record_id") or "")
                        if unit_id not in by_id:
                            continue
                        keyword = float(result.get("_keyword_score") or 0.0)
                        semantic = float(result.get("_semantic_score") or 0.0)
                        score = float(result.get("_score") or 0.0)
                        hits.append(
                            SearchHit(
                                knowledge_unit_id=unit_id,
                                score=score,
                                score_breakdown={
                                    "native_rrf": score,
                                    "native_keyword": keyword,
                                    "native_semantic": semantic,
                                },
                                matched_by=["native_seekdb_hybrid", "rrf", field],
                                warnings=[
                                    "generated_sql_sha256="
                                    + hashlib.sha256(trace.generated_sql.encode()).hexdigest()
                                ],
                            )
                        )
                    if hits:
                        return hits
                except Exception as exc:  # noqa: BLE001 - deterministic fallback below
                    self._degraded_features.add("native_hybrid_sql")
                    self._rerank_unavailable_reason = (
                        f"native hybrid query degraded: {type(exc).__name__}"
                    )
                break

        exact_scores = {
            unit_id: exact_score(
                query, "\n".join((unit.title, unit.problem, unit.mechanism, unit.implementation))
            )
            for unit_id, unit in by_id.items()
        }
        rankings = [sorted(by_id, key=lambda item: (-exact_scores[item], item))]
        vector_scores: dict[str, dict[str, float]] = {}
        fulltext_scores: dict[str, dict[str, float]] = {}
        warnings = ["AI_RERANK unavailable; native SeekDB/RRF result used"]
        for analyzer, collection in sorted(self._fulltext_collections.items()):
            try:
                result = collection.hybrid_search(
                    query={
                        "where_document": {"$contains": query},
                        "n_results": max(limit * 4, 20),
                    },
                    rank={"rrf": {"rank_window_size": max(limit * 4, 20)}},
                    n_results=max(limit * 4, 20),
                    include=["documents", "metadatas"],
                )
                ids = (result.get("ids") or [[]])[0]
                filtered_ids = [item for item in ids if item in by_id]
                rankings.append(filtered_ids)
                fulltext_scores[analyzer] = {
                    item: 1.0 / rank for rank, item in enumerate(filtered_ids, start=1)
                }
            except Exception as exc:  # noqa: BLE001
                self._degraded_features.add(f"fulltext_{analyzer}")
                warnings.append(f"fulltext_{analyzer} degraded: {type(exc).__name__}")
        for field, vector in sorted((query_vectors or {}).items()):
            if field not in VECTOR_FIELDS or not vector:
                continue
            try:
                result = self._vector_collection(field, len(vector)).query(
                    query_embeddings=vector,
                    n_results=max(limit * 4, 20),
                    include=["distances"],
                )
                ids = (result.get("ids") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                ids = [item for item in ids if item in by_id]
                rankings.append(ids)
                vector_scores[field] = {
                    item: 1.0 - float(distance)
                    for item, distance in zip(ids, distances, strict=False)
                }
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"vector_{field} degraded: {type(exc).__name__}")
        rrf = reciprocal_rank_fusion(rankings)
        hits = []
        for unit_id in by_id:
            breakdown = {"exact": exact_scores[unit_id], "rrf": rrf.get(unit_id, 0.0)}
            matched_by = ["exact_or_fulltext"] if exact_scores[unit_id] else []
            for analyzer, scores in fulltext_scores.items():
                if unit_id in scores:
                    label = "bm25_beng" if analyzer == "beng" else f"fulltext_{analyzer}"
                    breakdown[label] = scores[unit_id]
                    matched_by.append(label)
            for field, scores in vector_scores.items():
                if unit_id in scores:
                    breakdown[f"vector_{field}"] = scores[unit_id]
                    matched_by.append(f"vector_{field}")
            hits.append(
                SearchHit(
                    knowledge_unit_id=unit_id,
                    score=exact_scores[unit_id] * 0.7 + rrf.get(unit_id, 0.0) * 0.3,
                    score_breakdown=breakdown,
                    matched_by=matched_by,
                    warnings=warnings,
                )
            )
        return sorted(hits, key=lambda item: (-item.score, item.knowledge_unit_id))[:limit]

    def put_reference_pack(self, pack: ReferencePackV2) -> bool:
        return self._put_payload("reference_pack", pack.reference_pack_id, pack)

    def get_reference_pack(self, reference_pack_id: str) -> ReferencePackV2 | None:
        payload = self._get_payload("reference_pack", reference_pack_id)
        return _parse(ReferencePackV2, payload) if payload else None

    def iter_reference_packs(self):
        packs = [
            _parse(ReferencePackV2, value)
            for value in self._iter_payloads("reference_pack")
        ]
        yield from sorted(packs, key=lambda item: item.reference_pack_id)

    def put_feedback(self, feedback: KnowledgeUsageFeedbackV1) -> bool:
        existing = self._get_payload("feedback", feedback.feedback_id)
        payload = feedback.model_dump(mode="json", exclude_none=False)
        if existing is not None and existing != payload:
            raise ValueError(f"feedback ID conflict: {feedback.feedback_id}")
        created = self._put_payload("feedback", feedback.feedback_id, feedback)
        governance = governance_for_feedback(feedback)
        self._put_payload(
            "feedback_governance",
            governance.governance_id,
            governance,
            metadata={
                "queue": governance.queue,
                "status": governance.status,
                "requires_human_review": governance.requires_human_review,
            },
        )
        return created

    def get_feedback_governance(
        self, governance_id: str
    ) -> FeedbackGovernanceRecordV1 | None:
        payload = self._get_payload("feedback_governance", governance_id)
        return _parse(FeedbackGovernanceRecordV1, payload) if payload else None

    def list_feedback_governance(
        self, *, queue: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[FeedbackGovernanceRecordV1]:
        if limit <= 0:
            return []
        records = [
            _parse(FeedbackGovernanceRecordV1, value)
            for value in self._iter_payloads("feedback_governance")
        ]
        records = [
            item
            for item in records
            if (queue is None or item.queue == queue) and (status is None or item.status == status)
        ]
        records.sort(key=lambda item: (item.created_at, item.governance_id), reverse=True)
        return records[:limit]

    def review_feedback_governance(
        self, governance_id: str, *, decision: str
    ) -> FeedbackGovernanceRecordV1 | None:
        if decision not in {"apply", "reject"}:
            raise ValueError("decision must be 'apply' or 'reject'")
        record = self.get_feedback_governance(governance_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={"status": "reviewed" if decision == "apply" else "dismissed"}
        )
        self._put_payload(
            "feedback_governance",
            governance_id,
            updated,
            metadata={
                "queue": updated.queue,
                "status": updated.status,
                "requires_human_review": updated.requires_human_review,
            },
        )
        return updated

    def put_index_version(self, version: IndexVersionRecord) -> bool:
        existing = self._get_payload("index_version", version.index_version)
        payload = version.model_dump(mode="json", exclude_none=False)
        if existing is not None and existing != payload:
            raise ValueError(f"index version conflict: {version.index_version}")
        return self._put_payload("index_version", version.index_version, version)

    def latest_index_version(self) -> IndexVersionRecord | None:
        versions = [
            _parse(IndexVersionRecord, value) for value in self._iter_payloads("index_version")
        ]
        return max(versions, key=lambda item: item.created_at) if versions else None

    def statistics(self) -> dict[str, Any]:
        logical_names = {
            "source_count": "source",
            "snapshot_count": "snapshot",
            "project_count": "project_card",
            "wiki_page_count": "wiki_page",
            "knowledge_unit_count": "unit",
            "claim_count": "claim",
            "reference_pack_count": "reference_pack",
            "feedback_count": "feedback",
            "feedback_governance_count": "feedback_governance",
            "source_disagreement_count": "source_disagreement",
        }
        return {
            label: sum(1 for _ in self._iter_payloads(logical))
            for label, logical in logical_names.items()
        }

    def close(self) -> None:
        if self._native_connection is not None:
            self._native_connection.close()
            self._native_connection = None
        self._client.__exit__(None, None, None)
