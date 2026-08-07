from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from rosclaw_know.contracts import (
    EvidenceRefV2,
    IntegrityV2,
    KnowledgeUnitV2,
    KnowledgeVectorsV2,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.store import (
    DocumentRecord,
    ImmutableSnapshotError,
    InMemoryKnowStore,
    SearchFilters,
    StoreConfigurationError,
    guard_store_isolation,
)
from rosclaw_know.store.migrations import load_migrations

NOW = datetime(2026, 2, 3, tzinfo=UTC)
CONTENT = "Controller raises E42_TIMEOUT and clamps its integral term."
HASH = hashlib.sha256(CONTENT.encode()).hexdigest()


def populated_store() -> InMemoryKnowStore:
    store = InMemoryKnowStore()
    source = SourceRecordV2(
        source_id="source-1",
        canonical_url="https://example.invalid/repo",
        source_type="repository",
        title="Controller",
        trust_tier="primary",
        discovered_at=NOW,
    )
    snapshot = SourceSnapshotV2(
        snapshot_id="snap-1",
        source_id="source-1",
        version_kind="git_commit",
        version_value="abcdef1",
        commit_sha="abcdef1",
        fetched_at=NOW,
        content_hash=HASH,
        integrity=IntegrityV2(sha256=HASH),
    )
    document = DocumentRecord(
        document_id="doc-1",
        snapshot_id="snap-1",
        document_type="source_code",
        path="src/controller.py",
        title="controller.py",
        language="python",
        content=CONTENT,
        content_hash=HASH,
        size_bytes=len(CONTENT.encode()),
        created_at=NOW,
    )
    evidence = EvidenceRefV2(
        evidence_id="ev-1",
        source_id="source-1",
        snapshot_id="snap-1",
        document_id="doc-1",
        path="src/controller.py",
        start_line=1,
        end_line=1,
        url="https://example.invalid/repo/blob/abcdef1/src/controller.py#L1",
        content_hash=HASH,
        excerpt=CONTENT,
    )
    with store.transaction():
        assert store.upsert_source(source)
        assert store.put_snapshot(snapshot)
        assert store.put_document(document)
        assert store.put_evidence(evidence)
    return store


def make_unit(unit_id: str = "unit-1", *, title: str = "E42 timeout anti-windup"):
    store_evidence = EvidenceRefV2(
        evidence_id="ev-1",
        source_id="source-1",
        snapshot_id="snap-1",
        document_id="doc-1",
        path="src/controller.py",
        start_line=1,
        end_line=1,
        url="https://example.invalid/repo/blob/abcdef1/src/controller.py#L1",
        content_hash=HASH,
        excerpt=CONTENT,
    )
    return KnowledgeUnitV2(
        knowledge_unit_id=unit_id,
        unit_type="failure_lesson",
        title=title,
        problem="E42_TIMEOUT during stabilization",
        mechanism="Integral wind-up extends recovery.",
        implementation="Inspect and configure integral clamps.",
        applicability=["PID"],
        limitations=["Bounds require tuning"],
        software_constraints={"ros": "humble", "simulator": "isaac_lab"},
        robot_constraints=["unitree_g1"],
        source_snapshot_ids=["snap-1"],
        evidence_refs=[store_evidence],
        confidence=0.9,
        status="verified",
        created_at=NOW,
        updated_at=NOW,
        vectors=KnowledgeVectorsV2(problem=[1.0, 0.0], mechanism=[0.8, 0.2], content=[0.9, 0.1]),
    )


def test_idempotent_ingest_and_immutable_snapshot():
    store = populated_store()
    snapshot = store.get_snapshot("snap-1")
    assert snapshot is not None
    assert store.put_snapshot(snapshot) is False
    mutated = snapshot.model_copy(update={"content_hash": "b" * 64})
    with pytest.raises(ImmutableSnapshotError):
        store.put_snapshot(mutated)


def test_transaction_rolls_back_all_records():
    store = InMemoryKnowStore()
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.sources["partial"] = object()  # type: ignore[assignment]
            raise RuntimeError("fail")
    assert store.sources == {}


def test_same_semantic_content_does_not_rewrite_embeddings():
    store = populated_store()
    unit = make_unit()
    assert store.upsert_unit(unit)
    assert store.embedding_writes == 1
    metadata_only = unit.model_copy(update={"confidence": 0.8})
    assert store.upsert_unit(metadata_only)
    assert store.embedding_writes == 1
    assert store.get_unit("unit-1").confidence == 0.8  # type: ignore[union-attr]


def test_exact_error_precedes_vector_only_match_and_has_breakdown():
    store = populated_store()
    store.upsert_unit(make_unit())
    store.upsert_unit(make_unit("unit-2", title="Generic tuning guidance"))
    hits = store.search(
        "E42_TIMEOUT",
        query_vectors={"problem": [0.0, 1.0]},
        filters=SearchFilters(robot="unitree_g1", ros_distro="humble"),
    )
    assert hits[0].knowledge_unit_id == "unit-1"
    assert {"exact", "rrf", "vector_problem"} <= hits[0].score_breakdown.keys()
    assert any("AI_RERANK" in warning for warning in hits[0].warnings)


def test_unknown_compatibility_is_retained_for_explicit_warning_not_filtered():
    store = populated_store()
    generic = make_unit().model_copy(update={"robot_constraints": [], "software_constraints": {}})
    store.upsert_unit(generic)
    hits = store.search(
        "E42_TIMEOUT",
        filters=SearchFilters(robot="limo", ros_distro="melodic", simulator="gazebo"),
    )
    assert [hit.knowledge_unit_id for hit in hits] == ["unit-1"]


def test_know_memory_database_and_path_isolation(tmp_path):
    with pytest.raises(StoreConfigurationError, match="separate databases"):
        guard_store_isolation(know_database="rosclaw_know", memory_database="ROSCLAW_KNOW")
    shared = tmp_path / "shared"
    with pytest.raises(StoreConfigurationError, match="separate embedded paths"):
        guard_store_isolation(
            know_database="know",
            memory_database="memory",
            know_path=shared,
            memory_path=shared / ".",
        )


def test_migrations_are_contiguous_and_have_rollbacks():
    migrations = load_migrations("migrations/seekdb")
    assert [migration.version for migration in migrations] == [1, 2, 3, 4, 5, 6, 7]
    assert all(migration.up_sql and migration.down_sql for migration in migrations)
