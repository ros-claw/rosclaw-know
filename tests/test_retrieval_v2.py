from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from rosclaw_know.contracts import (
    EvidenceRefV2,
    IntegrityV2,
    KnowledgeUnitV2,
    KnowledgeVectorsV2,
    ReferenceContextV2,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.retrieval import ReferencePackBuilder, build_retrieval_plan
from rosclaw_know.store import DocumentRecord, IndexVersionRecord, InMemoryKnowStore

NOW = datetime(2026, 4, 1, tzinfo=UTC)
CONTENT = "raise E42_TIMEOUT when integral limit is exceeded"
HASH = hashlib.sha256(CONTENT.encode()).hexdigest()


def make_store() -> InMemoryKnowStore:
    store = InMemoryKnowStore()
    store.upsert_source(
        SourceRecordV2(
            source_id="source-1",
            canonical_url="https://example.invalid/repo",
            source_type="repository",
            title="Controller",
            trust_tier="primary",
            discovered_at=NOW,
        )
    )
    store.put_snapshot(
        SourceSnapshotV2(
            snapshot_id="snap-1",
            source_id="source-1",
            version_kind="git_commit",
            version_value="abcdef1",
            commit_sha="abcdef1",
            fetched_at=NOW,
            content_hash=HASH,
            integrity=IntegrityV2(sha256=HASH),
        )
    )
    store.put_document(
        DocumentRecord(
            document_id="doc-1",
            snapshot_id="snap-1",
            document_type="source_code",
            path="src/controller.py",
            title="controller.py",
            language="python",
            content=CONTENT,
            content_hash=HASH,
            size_bytes=len(CONTENT),
            created_at=NOW,
        )
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
    store.put_evidence(evidence)
    for unit_id, title, vector in (
        ("unit-exact", "E42_TIMEOUT integral failure", [1.0, 0.0]),
        ("unit-generic", "Generic controller tuning", [0.0, 1.0]),
    ):
        store.upsert_unit(
            KnowledgeUnitV2(
                knowledge_unit_id=unit_id,
                unit_type="failure_lesson",
                title=title,
                problem=title,
                mechanism="Integral accumulation delays recovery.",
                implementation="Inspect the clamp before changing gains.",
                applicability=["PID"],
                limitations=["Requires a measured bound"],
                software_constraints={
                    "ros": "humble",
                    "simulator": "isaac_lab",
                    "torch": "1.0",
                },
                robot_constraints=["unitree_g1"],
                source_snapshot_ids=["snap-1"],
                evidence_refs=[evidence],
                confidence=0.9,
                status="verified",
                created_at=NOW,
                updated_at=NOW,
                vectors=KnowledgeVectorsV2(problem=vector),
            )
        )
    store.put_index_version(
        IndexVersionRecord(
            index_version="index-2026-04",
            embedding_model="fixture",
            embedding_dimension=2,
            schema_version="rosclaw.know.store.v2",
            source_snapshot_hash=HASH,
            created_at=NOW,
        )
    )
    return store


class FixtureEmbedding:
    def embed(self, texts):
        return {field: [0.0, 1.0] for field in texts}


def test_planner_prioritizes_exact_error_tokens():
    plan = build_retrieval_plan(
        "controller returned E42_TIMEOUT -5",
        ReferenceContextV2(robot="unitree_g1"),
    )
    assert "E42_TIMEOUT" in plan.exact_terms
    assert "-5" in plan.exact_terms
    assert plan.semantic_queries["code"]


def test_reference_pack_is_evidence_pinned_explainable_and_compatible():
    store = make_store()
    pack = ReferencePackBuilder(store, embedding_provider=FixtureEmbedding()).retrieve(
        query="E42_TIMEOUT",
        context=ReferenceContextV2(
            task="stabilize",
            robot="unitree_g1",
            simulator="isaac_lab",
            ros_distro="humble",
            software_versions={"torch": "2.0"},
        ),
        top_k=2,
        token_budget=10_000,
    )
    assert pack.items[0].knowledge_unit_ids == ["unit-exact"]
    assert pack.items[0].exact_files == ["src/controller.py"]
    assert pack.items[0].evidence_refs[0].snapshot_id == "snap-1"
    assert pack.items[0].source_version == "git_commit:abcdef1"
    assert "compatibility" in pack.items[0].score_breakdown
    assert any("torch mismatch" in item for item in pack.items[0].incompatibilities)
    assert any("reranker_used=false" in warning for warning in pack.warnings)
    assert store.get_reference_pack(pack.reference_pack_id) == pack


def test_progressive_disclosure_sets_cursor_instead_of_overrunning_budget():
    pack = ReferencePackBuilder(make_store()).retrieve(
        query="E42_TIMEOUT",
        context=ReferenceContextV2(robot="unitree_g1", ros_distro="humble"),
        top_k=2,
        token_budget=10,
    )
    assert pack.items == []
    assert pack.truncated is True
    assert pack.continuation_cursor == "unit-exact"
