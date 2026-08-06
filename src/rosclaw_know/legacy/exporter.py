"""Byte-deterministic legacy exports from the canonical Know store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from rosclaw_know.store import KnowStore


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


@dataclass(frozen=True)
class LegacyExportReport:
    index_version: str
    bridge_path: Path
    pattern_count: int
    bridge_sha256: str


def render_legacy_assets(store: KnowStore) -> tuple[bytes, dict[str, bytes], str]:
    index = store.latest_index_version()
    index_version = index.index_version if index else "unversioned"
    clusters = {}
    patterns = {}
    for unit in sorted(store.iter_units(), key=lambda item: item.knowledge_unit_id):
        pattern_id = f"pattern_{unit.knowledge_unit_id}"
        clusters[unit.knowledge_unit_id] = {
            "associated_patterns": [pattern_id],
            "cross_domain_analogies": [
                {
                    "action_suggestion": unit.implementation,
                    "insight": unit.mechanism,
                    "source_domain": "KnowV2",
                }
            ],
            "domain": unit.applicability[0] if unit.applicability else "Unknown",
            "metadata": {
                "knowledge_unit_id": unit.knowledge_unit_id,
                "provenance_status": unit.provenance_status,
                "source_snapshot_ids": sorted(unit.source_snapshot_ids),
                "status": unit.status,
            },
            "standard_name": unit.problem,
        }
        pattern_body = (
            f"# {unit.title}\n\n"
            f"Provenance: {unit.provenance_status}\n\n"
            f"## Problem\n\n{unit.problem}\n\n"
            f"## Mechanism\n\n{unit.mechanism}\n\n"
            f"## Implementation\n\n{unit.implementation}\n"
        ).encode()
        patterns[f"{pattern_id}.md"] = pattern_body
    bridge = {
        "schema_version": "v2",
        "export_index_version": index_version,
        "symptom_clusters": clusters,
    }
    return _json_bytes(bridge), patterns, index_version


def export_legacy_assets(store: KnowStore, output_dir: str | Path) -> LegacyExportReport:
    output_dir = Path(output_dir)
    patterns_dir = output_dir / "code_patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)
    bridge_bytes, patterns, index_version = render_legacy_assets(store)
    bridge_path = output_dir / "bridge_index.json"
    bridge_path.write_bytes(bridge_bytes)
    for name, payload in sorted(patterns.items()):
        (patterns_dir / name).write_bytes(payload)
    return LegacyExportReport(
        index_version=index_version,
        bridge_path=bridge_path,
        pattern_count=len(patterns),
        bridge_sha256=hashlib.sha256(bridge_bytes).hexdigest(),
    )
