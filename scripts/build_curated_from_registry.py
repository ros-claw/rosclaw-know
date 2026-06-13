#!/usr/bin/env python3
"""scripts/build_curated_from_registry.py — build bridge + code_patterns from YAML registry.

Outputs a **curated-only** asset snapshot to a target directory. This is the
Sprint 1 reference build used by ``compare_registry_to_legacy.py``; the full
publish path still goes through ``curated_publisher.publish_curated_assets``
once ``ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1`` is set.

Usage::

    python scripts/build_curated_from_registry.py --out-dir /tmp/curated_assets
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.curated_publisher import (  # noqa: E402
    compute_cluster_content_hash,
)
from rosclaw_know.curated_registry import (  # noqa: E402
    CuratedRegistryEntry,
    load_registry,
    registry_root,
)


def _build_unified_diff(entry: CuratedRegistryEntry) -> str:
    before = (entry.body.before_code or "").splitlines(keepends=True)
    after = (entry.body.after_code or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        before,
        after,
        fromfile=f"{entry.id}.before.py",
        tofile=f"{entry.id}.after.py",
        lineterm="",
    )
    return "".join(diff)


def _write_pattern_md(entry: CuratedRegistryEntry, out_dir: Path) -> Path:
    out_path = out_dir / f"{entry.id}.md"
    diff = _build_unified_diff(entry)

    body = [
        "---",
        f"pattern_id: {entry.id}",
        f"safety_label: {entry.safety_label}",
        f"applicable_symptoms: [{entry.id}]",
        f"domain: {entry.domain}",
        "source: curated",
        "---",
        "",
        f"# {entry.standard_name}",
        "",
        f"**Domain**: `{entry.domain}`",
        f"**Safety label**: `{entry.safety_label}`",
        "",
        "## Fix",
        "",
        entry.body.fix,
        "",
        "## Anti-pattern",
        "",
        entry.body.anti_pattern,
        "",
    ]
    if entry.body.cross_domain_hints:
        body.append("## Cross-domain analogies (curated)")
        body.append("")
        for h in entry.body.cross_domain_hints:
            body.append(f"- **{h['source_domain']}** → {h['insight']}")
            body.append(f"  - related fix: {h['action_suggestion']}")
        body.append("")
    body.append("## Patch")
    body.append("")
    body.append("```diff")
    body.append(diff)
    body.append("```")
    out_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return out_path


def _build_cluster_entry(entry: CuratedRegistryEntry) -> dict[str, Any]:
    keyword_set = sorted(
        {
            entry.safety_label.lower(),
            entry.safety_label.replace("_", " ").lower(),
            *[k.lower() for k in entry.matched_keywords.include],
        }
    )
    cluster: dict[str, Any] = {
        "standard_name": entry.standard_name,
        "domain": entry.domain,
        "robot_type": entry.robot_type,
        "safety_label": entry.safety_label,
        "source": "curated",
        "source_tier": entry.source_tier,
        "status": entry.status,
        "runtime_eligible": entry.runtime_eligible,
        "matched_keywords": keyword_set,
        "cross_domain_analogies": [
            {
                "source_domain": h["source_domain"],
                "insight": h["insight"],
                "action_suggestion": h["action_suggestion"],
                "neighbor_id": "curated",
            }
            for h in entry.body.cross_domain_hints
        ],
        "associated_patterns": [entry.id],
        "topic_group": entry.topic_group,
        "topic_tag": entry.topic_tag,
        "log_signatures": entry.log_signatures,
        "routing_guard": entry.routing_guard.model_dump(mode="json"),
        "evidence": entry.evidence.model_dump(mode="json"),
        "demotion": entry.demotion.model_dump(mode="json"),
    }
    # Strip None fields for a compact cluster that still hashes stably.
    cluster = {k: v for k, v in cluster.items() if v is not None}
    cluster["content_hash"] = compute_cluster_content_hash(cluster)
    return cluster


def build_curated_assets(
    out_dir: Path, registry_root_override: Path | None = None
) -> dict[str, int]:
    """Generate a curated-only bridge_index.json + code_patterns/ under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    code_patterns_dir = out_dir / "code_patterns"
    code_patterns_dir.mkdir(parents=True, exist_ok=True)

    entries = load_registry(registry_root_override)

    clusters: dict[str, dict[str, Any]] = {}
    safety_lookup: dict[str, list[str]] = {}
    for entry in entries:
        clusters[entry.id] = _build_cluster_entry(entry)
        _write_pattern_md(entry, code_patterns_dir)
        safety_lookup.setdefault(entry.safety_label, []).append(entry.id)

    bridge = {
        "schema_version": "v2",
        "source": "curated_registry",
        "symptom_clusters": clusters,
        "safety_label_index": safety_lookup,
    }
    bridge_path = out_dir / "bridge_index.json"
    bridge_path.write_text(json.dumps(bridge, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "curated_clusters": len(clusters),
        "curated_patterns": len(list(code_patterns_dir.glob("*.md"))),
        "safety_label_entries": len(safety_lookup),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build curated-only assets from the YAML registry")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/assets_curated_from_registry"),
        help="Output directory for bridge_index.json + code_patterns/",
    )
    ap.add_argument(
        "--registry-root",
        type=Path,
        default=None,
        help="Override the registry root directory",
    )
    args = ap.parse_args()

    out_dir: Path = args.out_dir.resolve()
    print(f"[build] registry root: {args.registry_root or registry_root()}")
    print(f"[build] output dir:    {out_dir}")

    counts = build_curated_assets(out_dir, registry_root_override=args.registry_root)
    print(
        f"[build] clusters={counts['curated_clusters']} "
        f"patterns={counts['curated_patterns']} "
        f"safety_labels={counts['safety_label_entries']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
