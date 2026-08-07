from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from rosclaw_know.claims import audit_claims, compile_claims
from rosclaw_know.contracts import (
    IntegrityV2,
    ReferenceContextV2,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.operations import audit_project, doctor, freeze, project_diff, refresh_source
from rosclaw_know.retrieval import ReferencePackBuilder
from rosclaw_know.store import DocumentRecord, InMemoryKnowStore
from rosclaw_know.wiki import compile_project_wiki
from rosclaw_know.wiki.knowledge_units import compile_knowledge_units

NOW = datetime(2026, 8, 6, tzinfo=UTC)


def _snapshot(source_id: str, name: str, content: str, minute: int):
    digest = hashlib.sha256(content.encode()).hexdigest()
    return SourceSnapshotV2(
        snapshot_id=f"snapshot-{name}",
        source_id=source_id,
        version_kind="git_commit",
        version_value=name * 40,
        commit_sha=name * 40,
        fetched_at=NOW + timedelta(minutes=minute),
        content_hash=digest,
        parent_snapshot_id="snapshot-a" if name == "b" else None,
        supersedes_snapshot_id="snapshot-a" if name == "b" else None,
        integrity=IntegrityV2(sha256=digest),
    )


def _document(snapshot: SourceSnapshotV2, content: str):
    digest = hashlib.sha256(content.encode()).hexdigest()
    return DocumentRecord(
        document_id=f"document-{snapshot.snapshot_id}",
        snapshot_id=snapshot.snapshot_id,
        document_type="source_code",
        path="src/controller.py",
        title="controller.py",
        language="python",
        content=content,
        content_hash=digest,
        size_bytes=len(content.encode()),
        metadata={"url": f"https://example.invalid/{snapshot.version_value}/src/controller.py"},
        created_at=snapshot.fetched_at,
    )


def _seed():
    store = InMemoryKnowStore()
    source = SourceRecordV2(
        source_id="source-refresh",
        canonical_url="https://github.com/owner/project",
        source_type="repository",
        title="Refresh Project",
        publisher="owner",
        repository="owner/project",
        trust_tier="primary",
        discovered_at=NOW,
        latest_snapshot_id="snapshot-a",
    )
    content = "class OldController:\n    pass\n"
    snapshot = _snapshot(source.source_id, "a", content, 0)
    document = _document(snapshot, content)
    store.upsert_source(source)
    store.put_snapshot(snapshot)
    compilation = compile_project_wiki(
        source=source, snapshot=snapshot, documents=[document], store=store
    )
    units = compile_knowledge_units(compilation, store=store)
    compile_claims(compilation, units, source=source, store=store)
    pack = ReferencePackBuilder(store).retrieve(
        query="controller",
        context=ReferenceContextV2(task="inspect controller"),
        top_k=3,
    )
    return store, source, snapshot, document, compilation.project_card.project_id, pack


@pytest.mark.asyncio
async def test_refresh_defaults_to_dry_run_then_preserves_history_and_stales_pack():
    store, source, first_snapshot, _, project_id, pack = _seed()
    second_content = "class NewController:\n    pass\n"
    second_snapshot = _snapshot(source.source_id, "b", second_content, 1)
    second_document = _document(second_snapshot, second_content)

    class FixtureAdapter:
        async def snapshot(self, candidate):
            return second_snapshot

        async def fetch_documents(self, snapshot):
            yield second_document

    dry = await refresh_source(
        store, source_id=source.source_id, apply=False, adapter=FixtureAdapter()
    )
    assert dry["dry_run"] is True
    assert dry["changed_files"] == ["src/controller.py"]
    assert store.get_snapshot(second_snapshot.snapshot_id) is None

    applied = await refresh_source(
        store, source_id=source.source_id, apply=True, adapter=FixtureAdapter()
    )
    assert applied["dry_run"] is False
    assert store.get_snapshot(first_snapshot.snapshot_id) == first_snapshot
    assert store.get_document(pack.items[0].evidence_refs[0].document_id) is not None
    assert store.get_reference_pack(pack.reference_pack_id).stale is True
    diff = project_diff(
        store,
        project_id=project_id,
        from_snapshot=first_snapshot.snapshot_id,
        to_snapshot=second_snapshot.snapshot_id,
    )
    assert diff["files_changed"] == ["src/controller.py"]
    assert diff["claims_superseded"]
    assert audit_project(store, project_id)["ok"] is True


def test_doctor_and_freeze_report_separate_operational_capabilities():
    store, _, _, _, _, _ = _seed()
    report = doctor(store)
    assert report["migration_count"] == 7
    assert report["seekdb"]["rerank_support"] is False
    assert report["seekdb"]["rerank_unavailable_reason"]
    manifest = freeze(store, label="final-acceptance-fixture")
    assert manifest.source_snapshot_ids == ["snapshot-a"]
    assert manifest.claim_hash != "0" * 64
    assert manifest.schema_hash != manifest.migration_hash


def test_release_notes_compile_typed_temporal_claims_and_supersede_old_release():
    store = InMemoryKnowStore()
    source = SourceRecordV2(
        source_id="source-release",
        canonical_url="https://github.com/realsenseai/realsense-ros",
        source_type="repository",
        title="realsense-ros",
        publisher="realsenseai",
        repository="realsenseai/realsense-ros",
        trust_tier="primary",
        discovered_at=NOW,
    )
    store.upsert_source(source)

    def ingest(tag: str, body: str, minute: int, parent: str | None = None):
        content = json.dumps(
            [
                {
                    "tag_name": tag,
                    "name": tag,
                    "published_at": (NOW + timedelta(minutes=minute)).isoformat(),
                    "body": body,
                }
            ]
        )
        digest = hashlib.sha256(content.encode()).hexdigest()
        snapshot = SourceSnapshotV2(
            snapshot_id=f"snapshot-{tag}",
            source_id=source.source_id,
            version_kind="release",
            version_value=tag,
            tag=tag,
            fetched_at=NOW + timedelta(minutes=minute),
            content_hash=digest,
            parent_snapshot_id=parent,
            supersedes_snapshot_id=parent,
            integrity=IntegrityV2(sha256=digest),
        )
        document = DocumentRecord(
            document_id=f"document-{tag}",
            snapshot_id=snapshot.snapshot_id,
            document_type="release",
            path=".rosclaw/github/releases.json",
            title="releases.json",
            language="json",
            content=content,
            content_hash=digest,
            size_bytes=len(content.encode()),
            metadata={"url": f"https://github.com/realsenseai/realsense-ros/releases/{tag}"},
            created_at=snapshot.fetched_at,
        )
        store.put_snapshot(snapshot)
        compilation = compile_project_wiki(
            source=source,
            snapshot=snapshot,
            documents=[document],
            store=store,
        )
        units = compile_knowledge_units(compilation, store=store)
        return compile_claims(compilation, units, source=source, store=store), snapshot

    first, first_snapshot = ingest(
        "4.55.0",
        "Breaking migration: upgrade launch parameters. Deprecated old_api. "
        "Requires ROS Humble or newer.",
        0,
    )
    second, _ = ingest(
        "4.56.0",
        "Breaking migration: use new launch parameters. Deprecated old_api is removed. "
        "Requires ROS Jazzy for this feature.",
        1,
        first_snapshot.snapshot_id,
    )
    expected_types = {"migration_note", "deprecated_api", "compatibility_constraint"}
    assert expected_types <= {claim.claim_type for claim in first}
    assert expected_types <= {claim.claim_type for claim in second}
    for predicate in (
        "migration_note",
        "deprecated_api",
        "release_compatibility_constraint",
    ):
        old = next(claim for claim in first if claim.predicate == predicate)
        new = next(claim for claim in second if claim.predicate == predicate)
        assert store.get_claim(old.claim_id).status == "superseded"
        assert store.get_claim(old.claim_id).superseded_by == [new.claim_id]
    assert audit_claims(store, second).ok
