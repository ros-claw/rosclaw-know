from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from rosclaw_know.claims import audit_claims, compile_claims, put_claim_with_governance
from rosclaw_know.contracts import (
    EvidenceRefV2,
    IntegrityV2,
    KnowledgeClaimV1,
    KnowledgeUnitV2,
    KnowledgeUsageFeedbackV1,
    ReferenceContextV2,
    SourceRecordV2,
    SourceSnapshotV2,
    TruthQualityV1,
)
from rosclaw_know.retrieval import ReferencePackBuilder
from rosclaw_know.store import DocumentRecord, InMemoryKnowStore
from rosclaw_know.wiki import compile_project_wiki
from rosclaw_know.wiki.knowledge_units import compile_knowledge_units

NOW = datetime(2026, 8, 6, tzinfo=UTC)


def _document(snapshot_id: str, content: str) -> DocumentRecord:
    digest = hashlib.sha256(content.encode()).hexdigest()
    return DocumentRecord(
        document_id=f"doc-{snapshot_id}",
        snapshot_id=snapshot_id,
        document_type="source_code",
        path="src/controller.py",
        title="controller.py",
        language="python",
        content=content,
        content_hash=digest,
        size_bytes=len(content.encode()),
        metadata={"url": f"https://example.invalid/{snapshot_id}/src/controller.py"},
        created_at=NOW,
    )


def _snapshot(source_id: str, snapshot_id: str, content: str, offset: int):
    digest = hashlib.sha256(content.encode()).hexdigest()
    return SourceSnapshotV2(
        snapshot_id=snapshot_id,
        source_id=source_id,
        version_kind="git_commit",
        version_value=("a" if offset == 0 else "b") * 40,
        commit_sha=("a" if offset == 0 else "b") * 40,
        fetched_at=NOW + timedelta(minutes=offset),
        content_hash=digest,
        parent_snapshot_id="snapshot-a" if offset else None,
        supersedes_snapshot_id="snapshot-a" if offset else None,
        integrity=IntegrityV2(sha256=digest),
    )


def test_claims_are_evidence_closed_temporal_and_not_promoted_by_usage():
    store = InMemoryKnowStore()
    source = SourceRecordV2(
        source_id="source-primary",
        canonical_url="https://example.invalid/project",
        source_type="repository",
        title="Temporal Project",
        publisher="Maintainer",
        repository="owner/project",
        trust_tier="primary",
        discovered_at=NOW,
    )
    store.upsert_source(source)
    first_content = "class OldController:\n    pass\n"
    first_snapshot = _snapshot(source.source_id, "snapshot-a", first_content, 0)
    first_document = _document(first_snapshot.snapshot_id, first_content)
    store.put_snapshot(first_snapshot)
    first_compilation = compile_project_wiki(
        source=source,
        snapshot=first_snapshot,
        documents=[first_document],
        store=store,
    )
    first_units = compile_knowledge_units(first_compilation, store=store)
    first_claims = compile_claims(first_compilation, first_units, source=source, store=store)
    assert audit_claims(store, first_claims).ok
    component_count = next(
        item for item in first_claims if item.predicate == "indexed_component_count"
    )
    assert component_count.evidence_refs[0].path == ".rosclaw/repo_facts.json"

    second_content = "class NewController:\n    pass\n"
    second_snapshot = _snapshot(source.source_id, "snapshot-b", second_content, 1)
    second_document = _document(second_snapshot.snapshot_id, second_content)
    store.put_snapshot(second_snapshot)
    second_compilation = compile_project_wiki(
        source=source,
        snapshot=second_snapshot,
        documents=[second_document],
        previous_documents=[first_document],
        store=store,
    )
    second_units = compile_knowledge_units(second_compilation, store=store)
    second_claims = compile_claims(second_compilation, second_units, source=source, store=store)
    assert audit_claims(store, second_claims).ok

    old_symbol_claim = next(
        item
        for item in store.list_claims()
        if item.predicate == "defines_symbols" and "OldController" in item.object
    )
    new_symbol_claim = next(
        item
        for item in store.list_claims()
        if item.predicate == "defines_symbols" and "NewController" in item.object
    )
    assert old_symbol_claim.status == "superseded"
    assert old_symbol_claim.superseded_by == [new_symbol_claim.claim_id]
    assert old_symbol_claim.valid_to == new_symbol_claim.observed_at
    assert new_symbol_claim.truth_quality.source_authority == "S"

    truth_before = new_symbol_claim.truth_quality
    store.put_feedback(
        KnowledgeUsageFeedbackV1(
            feedback_id="feedback-useful",
            reference_pack_id="pack-fixture",
            knowledge_unit_id=new_symbol_claim.knowledge_unit_id,
            verdict="useful",
            context_hash="c" * 64,
            origin="user",
            created_at=NOW,
        )
    )
    assert store.get_claim(new_symbol_claim.claim_id).truth_quality == truth_before


def test_cross_source_conflict_is_reviewed_instead_of_silently_superseded():
    store = InMemoryKnowStore()
    claims: list[KnowledgeClaimV1] = []
    for suffix, object_value in (("a", "ROS 2 Humble"), ("b", "ROS 2 Jazzy")):
        source_id = f"source-{suffix}"
        snapshot_id = f"snapshot-{suffix}"
        content = f"Supported ROS distribution: {object_value}\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        store.upsert_source(
            SourceRecordV2(
                source_id=source_id,
                canonical_url=f"https://example.invalid/{suffix}",
                source_type="repository",
                title=f"Source {suffix.upper()}",
                trust_tier="primary",
                discovered_at=NOW,
            )
        )
        snapshot = SourceSnapshotV2(
            snapshot_id=snapshot_id,
            source_id=source_id,
            version_kind="git_commit",
            version_value=suffix * 40,
            commit_sha=suffix * 40,
            fetched_at=NOW,
            content_hash=digest,
            integrity=IntegrityV2(sha256=digest),
        )
        document = _document(snapshot_id, content)
        evidence = EvidenceRefV2(
            evidence_id=f"evidence-{suffix}",
            source_id=source_id,
            snapshot_id=snapshot_id,
            document_id=document.document_id,
            path=document.path,
            start_line=1,
            end_line=1,
            url=f"https://example.invalid/{suffix}/src/controller.py#L1",
            content_hash=digest,
            excerpt=content.strip(),
        )
        unit = KnowledgeUnitV2(
            knowledge_unit_id=f"unit-{suffix}",
            unit_type="compatibility",
            title=f"Compatibility from source {suffix.upper()}",
            problem="Choose the supported ROS distribution.",
            mechanism=content.strip(),
            implementation="Verify the source before use.",
            source_snapshot_ids=[snapshot_id],
            evidence_refs=[evidence],
            confidence=1.0,
            status="verified",
            created_at=NOW,
            updated_at=NOW,
        )
        store.put_snapshot(snapshot)
        store.put_document(document)
        store.put_evidence(evidence)
        store.upsert_unit(unit)
        claim = KnowledgeClaimV1(
            claim_id=f"claim-{suffix}",
            knowledge_unit_id=unit.knowledge_unit_id,
            subject="owner/project",
            predicate="supported_ros_distribution",
            object=object_value,
            claim_type="compatibility_constraint",
            source_snapshot_ids=[snapshot_id],
            evidence_refs=[evidence],
            truth_quality=TruthQualityV1(
                score=1.0,
                source_authority="S",
                direct_evidence=True,
            ),
            observed_at=NOW,
            generated_by="test",
            attributed_to=f"Source {suffix.upper()}",
            created_at=NOW,
            updated_at=NOW,
        )
        claims.append(put_claim_with_governance(store, claim))

    persisted = [store.get_claim(item.claim_id) for item in claims]
    assert all(item is not None and item.status == "active" for item in persisted)
    assert all(
        item is not None and not item.truth_quality.contradiction_resolved
        for item in persisted
    )
    assert persisted[0] is not None and persisted[0].contradicts == ["claim-b"]
    assert persisted[1] is not None and persisted[1].contradicts == ["claim-a"]
    disagreements = store.list_source_disagreements(status="pending_review")
    assert len(disagreements) == 1
    assert disagreements[0].claim_ids == ["claim-a", "claim-b"]

    builder = ReferencePackBuilder(store)
    pack = builder.retrieve(
        query="supported ROS distribution",
        context=ReferenceContextV2(),
        top_k=10,
    )
    trace = builder.explain(
        query="supported ROS distribution",
        context=ReferenceContextV2(),
        top_k=10,
    )
    assert pack.items == []
    assert set(pack.warnings) >= {
        "contradiction_guard_rejected_for_use:unit-a",
        "contradiction_guard_rejected_for_use:unit-b",
    }
    assert {candidate.knowledge_unit_id for candidate in trace.candidates} == {
        "unit-a",
        "unit-b",
    }
    assert all(
        candidate.rejected_reasons == ["unresolved_contradiction"]
        for candidate in trace.candidates
    )
