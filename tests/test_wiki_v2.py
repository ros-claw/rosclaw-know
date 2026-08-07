from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from rosclaw_know.contracts import IntegrityV2, SourceRecordV2, SourceSnapshotV2
from rosclaw_know.store import DocumentRecord, InMemoryKnowStore
from rosclaw_know.wiki import compile_project_wiki

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def make_source_snapshot(snapshot_id="snap-1", content_hash="a" * 64):
    source = SourceRecordV2(
        source_id="source-1",
        canonical_url="https://github.com/example/g1-control",
        source_type="repository",
        title="G1 Control",
        repository="example/g1-control",
        license="Apache-2.0",
        trust_tier="primary",
        discovered_at=NOW,
    )
    snapshot = SourceSnapshotV2(
        snapshot_id=snapshot_id,
        source_id="source-1",
        version_kind="git_commit",
        version_value="abcdef1" if snapshot_id == "snap-1" else "abcdef2",
        commit_sha="abcdef1" if snapshot_id == "snap-1" else "abcdef2",
        fetched_at=NOW,
        content_hash=content_hash,
        integrity=IntegrityV2(sha256=content_hash),
    )
    return source, snapshot


def document(snapshot_id, path, content, language=None):
    digest = hashlib.sha256(content.encode()).hexdigest()
    return DocumentRecord(
        document_id=f"doc-{hashlib.sha256((snapshot_id + path).encode()).hexdigest()[:12]}",
        snapshot_id=snapshot_id,
        document_type="source_code" if language else "documentation",
        path=path,
        title=path.rsplit("/", 1)[-1],
        language=language,
        content=content,
        content_hash=digest,
        size_bytes=len(content.encode()),
        metadata={"url": f"https://example.invalid/blob/{snapshot_id}/{path}"},
        created_at=NOW,
    )


def current_documents(snapshot_id="snap-1", reward="return velocity - torque"):
    return [
        document(
            snapshot_id,
            "README.md",
            "Unitree G1 humanoid football training in Isaac Lab with ROS Humble.",
            "markdown",
        ),
        document(
            snapshot_id,
            "src/train/reward.py",
            f"import torch\n\ndef reward(state):\n    {reward}\n",
            "python",
        ),
        document(
            snapshot_id,
            "deploy/robot.py",
            "def publish_action():\n    # ROS topic action space\n    return None\n",
            "python",
        ),
        document(snapshot_id, "pyproject.toml", "[project]\nname='g1-control'\n", "toml"),
    ]


def test_wiki_compiler_has_real_paths_pinned_evidence_and_static_symbols():
    source, snapshot = make_source_snapshot()
    store = InMemoryKnowStore()
    store.upsert_source(source)
    store.put_snapshot(snapshot)
    result = compile_project_wiki(
        source=source, snapshot=snapshot, documents=current_documents(), store=store
    )

    real_paths = {document.path for document in current_documents()}
    assert "unitree_g1" in result.inventory.robots
    assert "isaac_lab" in result.inventory.simulators
    assert result.project_card.source_snapshot_id == snapshot.snapshot_id
    assert result.pages
    assert all(page.evidence_refs for page in result.pages)
    assert all(
        evidence.snapshot_id == snapshot.snapshot_id and evidence.path in real_paths
        for page in result.pages
        for evidence in page.evidence_refs
    )
    src_component = next(item for item in result.components if item.path == "src")
    assert "reward" in src_component.public_symbols
    assert "torch" in src_component.dependencies
    assert store.get_project_card(result.project_card.project_id) == result.project_card
    assert len(store.list_wiki_pages(result.project_card.project_id)) == len(result.pages)
    facts_evidence = next(
        evidence
        for evidence in result.evidence_refs
        if evidence.path == ".rosclaw/repo_facts.json"
    )
    facts = store.get_document(facts_evidence.document_id)
    assert facts is not None
    assert facts.metadata["code_executed"] is False
    assert '"component_count": 4' in facts.content
    assert '"src"' in facts.content


def test_incremental_diff_only_marks_pages_using_changed_document():
    source, new_snapshot = make_source_snapshot("snap-2", "b" * 64)
    old_documents = current_documents("snap-1")
    new_documents = current_documents("snap-2", reward="return velocity - torque - slip")
    result = compile_project_wiki(
        source=source,
        snapshot=new_snapshot,
        documents=new_documents,
        previous_documents=old_documents,
    )
    assert result.changed_paths == ["src/train/reward.py"]
    assert "training" in result.rebuilt_page_types
    assert "architecture" in result.rebuilt_page_types
    assert "overview" not in result.rebuilt_page_types
    assert "deployment" not in result.rebuilt_page_types


def test_steering_excludes_paths_without_inventing_pages():
    source, snapshot = make_source_snapshot()
    docs = current_documents()
    docs.append(
        document(
            "snap-1",
            ".rosclaw/know.yaml",
            "schema_version: rosclaw.know.steer.v1\nexclude:\n  - deploy/**\n",
            "yaml",
        )
    )
    result = compile_project_wiki(source=source, snapshot=snapshot, documents=docs)
    assert "deploy/robot.py" not in result.inventory.paths
    assert all("deploy/robot.py" not in page.content for page in result.pages)
