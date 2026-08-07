"""Operational inspection, temporal refresh, diff, audit and freeze workflows."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rosclaw_know.claims import ClaimAuditResult, audit_claims, compile_claims
from rosclaw_know.contracts import PUBLIC_CONTRACTS
from rosclaw_know.contracts.base import export_contract_schemas
from rosclaw_know.sources import GitHubAdapter, SourceCandidate, default_source_registry
from rosclaw_know.store import KnowledgeIndexManifestV1, KnowStore
from rosclaw_know.store.migrations import load_migrations
from rosclaw_know.wiki import compile_project_wiki
from rosclaw_know.wiki.knowledge_units import compile_knowledge_units


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _project_id(canonical_url: str) -> str:
    return f"project_{hashlib.sha256(canonical_url.casefold().encode()).hexdigest()[:24]}"


def _source_documents(documents):
    return [
        document
        for document in documents
        if document.path != ".rosclaw/repo_facts.json"
    ]


def _package_version() -> str:
    # The source constant and wheel metadata are released together. Prefer the
    # constant so an editable checkout cannot report stale dist-info left by a
    # previous install.
    from rosclaw_know import __version__

    return __version__


def migration_root() -> Path:
    installed = Path(__file__).resolve().parent / "migrations" / "seekdb"
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[2] / "migrations" / "seekdb"


def migration_fingerprint() -> tuple[int, str]:
    migrations = load_migrations(migration_root())
    payload = [
        {
            "version": item.version,
            "name": item.name,
            "up": item.up_sql,
            "down": item.down_sql,
        }
        for item in migrations
    ]
    return len(migrations), _hash(payload)


def doctor(store: KnowStore) -> dict[str, Any]:
    migration_count, migration_hash = migration_fingerprint()
    index = store.latest_index_version()
    registry = default_source_registry()
    capabilities = store.capabilities
    stats = store.statistics()
    stale_sources = sum(
        1
        for source in store.iter_sources()
        if source.latest_snapshot_id is None or store.get_snapshot(source.latest_snapshot_id) is None
    )
    return {
        "status": "ok",
        "package_version": _package_version(),
        "contract_versions": sorted(
            item.SCHEMA_VERSION for item in PUBLIC_CONTRACTS if item.SCHEMA_VERSION
        ),
        "schema_version": index.schema_version if index else "unversioned",
        "migration_count": migration_count,
        "migration_hash": migration_hash,
        "seekdb": {
            "mode": capabilities.backend,
            "fulltext_indexes": capabilities.fulltext_analyzers,
            "hybrid_support": capabilities.hybrid_search,
            "native_hybrid_sql": capabilities.native_hybrid_sql,
            "rerank_support": capabilities.ai_rerank,
            "rerank_unavailable_reason": capabilities.rerank_unavailable_reason,
            "transactions": capabilities.transactions,
            "degraded": capabilities.degraded,
        },
        "index": index.model_dump(mode="json") if index else None,
        "source_adapters": {
            name: {"implementation": type(adapter).__name__, "available": "Unavailable" not in type(adapter).__name__}
            for name, adapter in sorted(registry.items())
        },
        "stale_sources": stale_sources,
        "review_queue": {
            "feedback": len(store.list_feedback_governance(status="pending_review", limit=1000)),
            "source_disagreement": len(
                store.list_source_disagreements(status="pending_review", limit=1000)
            ),
        },
        "latest_backup": None,
        "statistics": stats,
    }


def project_diff(
    store: KnowStore, *, project_id: str, from_snapshot: str, to_snapshot: str
) -> dict[str, Any]:
    if store.get_snapshot(from_snapshot) is None:
        raise ValueError(f"unknown from snapshot: {from_snapshot}")
    if store.get_snapshot(to_snapshot) is None:
        raise ValueError(f"unknown to snapshot: {to_snapshot}")
    before_documents = {
        item.path: item.content_hash
        for item in _source_documents(store.list_documents(from_snapshot))
    }
    after_documents = {
        item.path: item.content_hash
        for item in _source_documents(store.list_documents(to_snapshot))
    }
    files_changed = sorted(
        path
        for path in before_documents.keys() | after_documents.keys()
        if before_documents.get(path) != after_documents.get(path)
    )
    pages = store.list_wiki_pages(project_id)
    before_pages = {item.page_type: item.content_hash for item in pages if item.snapshot_id == from_snapshot}
    after_pages = {item.page_type: item.content_hash for item in pages if item.snapshot_id == to_snapshot}
    wiki_pages_changed = sorted(
        page_type
        for page_type in before_pages.keys() | after_pages.keys()
        if before_pages.get(page_type) != after_pages.get(page_type)
    )
    before_claims = {item.claim_id: item for item in store.list_claims(snapshot_id=from_snapshot)}
    after_claims = {item.claim_id: item for item in store.list_claims(snapshot_id=to_snapshot)}
    before_units = {
        item.knowledge_unit_id for item in store.iter_units() if from_snapshot in item.source_snapshot_ids
    }
    after_units = {
        item.knowledge_unit_id for item in store.iter_units() if to_snapshot in item.source_snapshot_ids
    }
    compatibility_before = {
        (item.subject, item.predicate): (
            item.compatibility_status,
            item.compatibility_scope.model_dump(mode="json"),
        )
        for item in before_claims.values()
    }
    compatibility_after = {
        (item.subject, item.predicate): (
            item.compatibility_status,
            item.compatibility_scope.model_dump(mode="json"),
        )
        for item in after_claims.values()
    }
    return {
        "project_id": project_id,
        "from_snapshot": from_snapshot,
        "to_snapshot": to_snapshot,
        "files_changed": files_changed,
        "wiki_pages_changed": wiki_pages_changed,
        "claims_added": sorted(after_claims.keys() - before_claims.keys()),
        "claims_removed": sorted(before_claims.keys() - after_claims.keys()),
        "claims_superseded": sorted(
            item.claim_id
            for item in before_claims.values()
            if item.status == "superseded" and set(item.superseded_by) & after_claims.keys()
        ),
        "compatibility_changes": [
            {"subject": key[0], "predicate": key[1], "from": compatibility_before.get(key), "to": compatibility_after.get(key)}
            for key in sorted(compatibility_before.keys() | compatibility_after.keys())
            if compatibility_before.get(key) != compatibility_after.get(key)
        ],
        "knowledge_units_added": sorted(after_units - before_units),
        "knowledge_units_removed": sorted(before_units - after_units),
    }


def mark_reference_packs_stale(store: KnowStore, snapshot_id: str) -> list[str]:
    changed = []
    for pack in store.iter_reference_packs():
        if not any(
            evidence.snapshot_id == snapshot_id
            for item in pack.items
            for evidence in item.evidence_refs
        ):
            continue
        warnings = list(
            dict.fromkeys(
                [
                    *pack.warnings,
                    f"source_refresh:snapshot_superseded:{snapshot_id}",
                    "reopen pinned evidence before implementation",
                ]
            )
        )
        store.put_reference_pack(
            pack.model_copy(update={"cached": True, "stale": True, "warnings": warnings})
        )
        changed.append(pack.reference_pack_id)
    return changed


async def refresh_source(
    store: KnowStore,
    *,
    source_id: str,
    apply: bool = False,
    adapter: GitHubAdapter | None = None,
) -> dict[str, Any]:
    source = store.get_source(source_id)
    if source is None:
        raise ValueError(f"unknown source: {source_id}")
    if not source.repository:
        raise ValueError("refresh currently requires a repository source")
    adapter = adapter or GitHubAdapter()
    candidate = SourceCandidate(
        source=source,
        adapter="github",
        snapshot_ref="HEAD",
        authority_score=0.95,
        qualification_score=1.0,
        metadata={"full_name": source.repository, "default_branch": "HEAD"},
    )
    snapshot = await adapter.snapshot(candidate)
    previous_snapshot_id = source.latest_snapshot_id
    if previous_snapshot_id == snapshot.snapshot_id:
        return {
            "source_id": source_id,
            "dry_run": not apply,
            "changed": False,
            "current_snapshot": previous_snapshot_id,
            "new_snapshot": snapshot.snapshot_id,
            "changed_files": [],
            "estimated_pages": 0,
            "estimated_embedding_work": 0,
        }
    documents = [item async for item in adapter.fetch_documents(snapshot)]
    previous_documents = (
        _source_documents(store.list_documents(previous_snapshot_id))
        if previous_snapshot_id
        else []
    )
    previous_hashes = {item.path: item.content_hash for item in previous_documents}
    current_hashes = {item.path: item.content_hash for item in documents}
    changed_files = sorted(
        path
        for path in previous_hashes.keys() | current_hashes.keys()
        if previous_hashes.get(path) != current_hashes.get(path)
    )
    estimate = {
        "source_id": source_id,
        "dry_run": not apply,
        "changed": True,
        "current_snapshot": previous_snapshot_id,
        "new_snapshot": snapshot.snapshot_id,
        "new_version": snapshot.version_value,
        "changed_files": changed_files,
        "estimated_pages": max(1, len({path.split("/", 1)[0] for path in changed_files})),
        "estimated_embedding_work": len(changed_files),
    }
    if not apply:
        return estimate
    store.put_snapshot(snapshot)
    compilation = compile_project_wiki(
        source=source,
        snapshot=snapshot,
        documents=documents,
        previous_documents=previous_documents or None,
        store=store,
    )
    units = compile_knowledge_units(compilation, store=store)
    claims = compile_claims(compilation, units, source=source, store=store)
    store.upsert_source(source.model_copy(update={"latest_snapshot_id": snapshot.snapshot_id}))
    stale_packs = mark_reference_packs_stale(store, previous_snapshot_id) if previous_snapshot_id else []
    return {
        **estimate,
        "rebuilt_page_types": compilation.rebuilt_page_types,
        "knowledge_units": len(units),
        "claims": len(claims),
        "stale_reference_packs": stale_packs,
    }


def audit_project(store: KnowStore, project_id: str) -> dict[str, Any]:
    card = store.get_project_card(project_id)
    if card is None:
        raise ValueError(f"unknown project: {project_id}")
    claims = [
        item
        for item in store.list_claims(snapshot_id=card.source_snapshot_id)
        if item.subject == project_id or item.subject.startswith(project_id + ":")
    ]
    result: ClaimAuditResult = audit_claims(store, claims)
    return {
        "project_id": project_id,
        "snapshot_id": card.source_snapshot_id,
        "claims_checked": result.checked,
        "claims_passed": result.passed,
        "failures": [item.model_dump(mode="json") for item in result.failures],
        "gate": {
            "invented_files": 0 if result.ok else None,
            "invented_symbols": 0 if result.ok else None,
            "missing_evidence": sum(
                item.reason in {"snapshot_missing", "document_missing", "evidence_record_mismatch"}
                for item in result.failures
            ),
            "snapshot_errors": sum(
                item.reason in {"snapshot_missing", "document_hash_mismatch"}
                for item in result.failures
            ),
        },
        "ok": result.ok,
    }


def freeze(store: KnowStore, *, label: str) -> KnowledgeIndexManifestV1:
    migration_count, migration_hash = migration_fingerprint()
    del migration_count
    snapshots = list(store.iter_snapshots())
    units = list(store.iter_units())
    claims = store.list_claims()
    pages = [page for source in store.iter_sources() for page in _pages_for_source(store, source)]
    index = store.latest_index_version()
    payload = {
        "label": label,
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "units": [item.model_dump(mode="json") for item in units],
        "claims": [item.model_dump(mode="json") for item in claims],
        "pages": [item.model_dump(mode="json") for item in pages],
    }
    created_at = datetime.now(UTC)
    manifest_id = f"index_manifest_{_hash(payload)[:24]}"
    return KnowledgeIndexManifestV1(
        manifest_id=manifest_id,
        label=label,
        schema_hash=_hash(export_contract_schemas(PUBLIC_CONTRACTS)),
        migration_hash=migration_hash,
        source_snapshot_ids=sorted(item.snapshot_id for item in snapshots),
        source_snapshot_hash=_hash([item.content_hash for item in snapshots]),
        embedding_model=index.embedding_model if index else "unconfigured",
        embedding_dimension=index.embedding_dimension if index else 0,
        index_version=index.index_version if index else "unversioned",
        wiki_hash=_hash([item.content_hash for item in pages]),
        knowledge_unit_hash=_hash([item.model_dump(mode="json") for item in units]),
        claim_hash=_hash([item.model_dump(mode="json") for item in claims]),
        compiler_version=_package_version(),
        created_at=created_at,
    )


def _pages_for_source(store: KnowStore, source: Any):
    if not source.latest_snapshot_id:
        return []
    return [
        page
        for page in store.list_wiki_pages(_project_id(source.canonical_url))
        if page.snapshot_id == source.latest_snapshot_id
    ]


__all__ = [
    "audit_project",
    "doctor",
    "freeze",
    "mark_reference_packs_stale",
    "project_diff",
    "refresh_source",
]
