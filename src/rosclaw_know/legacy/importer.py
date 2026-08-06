"""Deterministic bridge/code-pattern migration into the canonical Know store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rosclaw_know.contracts import (
    EvidenceRefV2,
    IntegrityV2,
    KnowledgeUnitV2,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.store import DocumentRecord, IndexVersionRecord, KnowStore

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_id(value: str) -> str:
    clean = "".join(
        character if character.isalnum() or character in "_-" else "_" for character in value
    )
    clean = clean.strip("_")[:120]
    return clean or _sha256(value.encode())[:24]


def _pattern_files(patterns_dir: Path | None) -> dict[str, Path]:
    if patterns_dir is None or not patterns_dir.is_dir():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(patterns_dir.glob("*.md")):
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            continue
        result[path.stem] = path
        result[path.stem.replace("pattern_v2_", "pattern_")] = path
    return result


@dataclass(frozen=True)
class LegacyImportReport:
    source_id: str
    snapshot_id: str
    units_seen: int
    units_created: int
    documents_created: int
    provenance_status: str = "legacy_unknown"
    origin: str = "legacy_asset"


def import_legacy_assets(
    store: KnowStore,
    *,
    bridge_path: str | Path,
    patterns_dir: str | Path | None = None,
    origin: str = "legacy_asset",
) -> LegacyImportReport:
    """Import legacy assets without inventing upstream provenance.

    Timestamps are the Unix epoch and identifiers are content-derived so the
    same bytes produce idempotent rows and an identical index version.
    """

    bridge_path = Path(bridge_path)
    if bridge_path.is_symlink() or not bridge_path.is_file():
        raise ValueError("bridge_path must be a regular non-symlink file")
    bridge_bytes = bridge_path.read_bytes()
    if len(bridge_bytes) > 50_000_000:
        raise ValueError("bridge_index exceeds the 50 MB migration limit")
    raw = json.loads(bridge_bytes)
    clusters = raw.get("symptom_clusters")
    if not isinstance(clusters, dict):
        raise ValueError("bridge_index must contain an object symptom_clusters")
    content_hash = _sha256(bridge_bytes)
    source_id = f"source_legacy_{content_hash[:24]}"
    snapshot_id = f"snapshot_legacy_{content_hash[:24]}"
    origin_label = "offline_bundle" if origin == "offline_bundle" else "legacy_asset"
    source = SourceRecordV2(
        source_id=source_id,
        canonical_url=f"{origin_label}://bridge_index/{content_hash}",
        source_type="legacy_bridge",
        title="Legacy ROSClaw bridge index",
        license="unknown",
        trust_tier="unknown",
        discovered_at=_EPOCH,
        latest_snapshot_id=snapshot_id,
        tags=[origin_label, "legacy_unknown"],
        provenance_status="legacy_unknown",
    )
    snapshot = SourceSnapshotV2(
        snapshot_id=snapshot_id,
        source_id=source_id,
        version_kind="document_version",
        version_value=f"{origin_label}:{content_hash}",
        fetched_at=_EPOCH,
        content_hash=content_hash,
        integrity=IntegrityV2(sha256=content_hash),
    )
    bridge_document_id = f"document_legacy_{content_hash[:24]}"
    bridge_document = DocumentRecord(
        document_id=bridge_document_id,
        snapshot_id=snapshot_id,
        document_type="legacy_bridge_index",
        path="bridge_index.json",
        title="Legacy bridge index",
        content=bridge_bytes.decode("utf-8"),
        content_hash=content_hash,
        size_bytes=len(bridge_bytes),
        metadata={"provenance_status": "legacy_unknown", "origin": origin_label},
        created_at=_EPOCH,
    )
    patterns = _pattern_files(Path(patterns_dir) if patterns_dir else None)
    documents_created = 0
    units_created = 0
    with store.transaction():
        store.upsert_source(source)
        store.put_snapshot(snapshot)
        documents_created += int(store.put_document(bridge_document))
        for cluster_key, value in sorted(clusters.items()):
            if not isinstance(value, dict):
                continue
            key = _safe_id(str(cluster_key))
            problem = str(value.get("standard_name") or cluster_key).strip()
            if not problem:
                continue
            analogies = value.get("cross_domain_analogies") or []
            mechanism_parts = [
                str(item.get("insight", "")).strip()
                for item in analogies
                if isinstance(item, dict) and item.get("insight")
            ]
            action_parts = [
                str(item.get("action_suggestion", "")).strip()
                for item in analogies
                if isinstance(item, dict) and item.get("action_suggestion")
            ]
            evidence = []
            bridge_excerpt = json.dumps(
                {"cluster_id": cluster_key, "standard_name": problem},
                ensure_ascii=False,
                sort_keys=True,
            )
            bridge_evidence = EvidenceRefV2(
                evidence_id=f"evidence_legacy_{key}_{content_hash[:12]}",
                source_id=source_id,
                snapshot_id=snapshot_id,
                document_id=bridge_document_id,
                path="bridge_index.json",
                section=f"symptom_clusters.{cluster_key}",
                url=f"{origin_label}://bridge_index/{content_hash}#{cluster_key}",
                content_hash=_sha256(bridge_excerpt.encode()),
                excerpt=bridge_excerpt[:2000],
            )
            store.put_evidence(bridge_evidence)
            evidence.append(bridge_evidence)

            pattern_texts = []
            for pattern_id in value.get("associated_patterns") or []:
                path = patterns.get(str(pattern_id))
                if path is None:
                    continue
                data = path.read_bytes()
                pattern_hash = _sha256(data)
                document_id = f"document_legacy_pattern_{pattern_hash[:24]}"
                document = DocumentRecord(
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    document_type="legacy_code_pattern",
                    path=f"code_patterns/{path.name}",
                    title=str(pattern_id),
                    content=data.decode("utf-8", errors="replace"),
                    content_hash=pattern_hash,
                    size_bytes=len(data),
                    metadata={"provenance_status": "legacy_unknown", "origin": origin_label},
                    created_at=_EPOCH,
                )
                documents_created += int(store.put_document(document))
                pattern_evidence = EvidenceRefV2(
                    evidence_id=f"evidence_legacy_pattern_{pattern_hash[:24]}",
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                    document_id=document_id,
                    path=document.path,
                    url=f"{origin_label}://code_patterns/{path.name}",
                    content_hash=pattern_hash,
                    excerpt=document.content[:2000] or "Empty legacy pattern file.",
                )
                store.put_evidence(pattern_evidence)
                evidence.append(pattern_evidence)
                pattern_texts.append(document.content[:4000])
            limitations = [
                "Upstream source provenance and license are unknown for this legacy asset."
            ]
            if origin_label == "offline_bundle":
                limitations.append("Offline bundle freshness is not guaranteed.")
            unit = KnowledgeUnitV2(
                knowledge_unit_id=f"unit_legacy_{key}_{content_hash[:12]}",
                unit_type="design_pattern",
                title=problem[:1000],
                problem=problem,
                mechanism="\n".join(mechanism_parts) or "Legacy mechanism was not recorded.",
                implementation=(
                    "\n".join(action_parts + pattern_texts)
                    or "Legacy implementation detail was not recorded."
                ),
                applicability=[
                    str(item)
                    for item in [
                        value.get("domain"),
                        value.get("topic_group"),
                        value.get("topic_tag"),
                    ]
                    if item
                ],
                limitations=limitations,
                source_snapshot_ids=[snapshot_id],
                evidence_refs=evidence,
                confidence=0.25,
                status="draft",
                provenance_status="legacy_unknown",
                created_at=_EPOCH,
                updated_at=_EPOCH,
            )
            units_created += int(store.upsert_unit(unit))
        store.put_index_version(
            IndexVersionRecord(
                index_version=f"idx_legacy_{content_hash[:24]}",
                embedding_model="legacy:none",
                embedding_dimension=1,
                schema_version="rosclaw.know.store.v2",
                source_snapshot_hash=content_hash,
                created_at=_EPOCH,
            )
        )
    return LegacyImportReport(
        source_id=source_id,
        snapshot_id=snapshot_id,
        units_seen=len(clusters),
        units_created=units_created,
        documents_created=documents_created,
        origin=origin_label,
    )
