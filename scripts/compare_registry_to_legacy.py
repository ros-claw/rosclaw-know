#!/usr/bin/env python3
"""scripts/compare_registry_to_legacy.py — semantic diff between registry-built and legacy curated assets.

The legacy bridge is the current ``data/assets/bridge_index.json`` produced by
``curated_publisher.py`` from the dataclass constants. The registry-built bridge
is produced by ``build_curated_from_registry.py``. This script checks that the
routing-critical fields are equivalent.

Usage::

    python scripts/compare_registry_to_legacy.py \
        --registry-assets /tmp/curated_assets_from_registry

Exit codes:
    0  semantically equivalent
    1  differences found
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.config import ASSETS_DIR  # noqa: E402

ROUTING_CRITICAL_FIELDS = {
    "standard_name",
    "domain",
    "topic_group",
    "topic_tag",
    "matched_keywords",
    "cross_domain_analogies",
    "associated_patterns",
}


def _load_clusters(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    clusters = data.get("symptom_clusters", {})
    return {
        cid: c
        for cid, c in clusters.items()
        if isinstance(c, dict) and c.get("source") == "curated"
    }


def _norm_keywords(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(v).strip().lower() for v in value}
    return set()


def _norm_analogies(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "source_domain": str(item.get("source_domain", "")).strip(),
                "insight": str(item.get("insight", "")).strip(),
                "action_suggestion": str(item.get("action_suggestion", "")).strip(),
            }
        )
    return sorted(out, key=lambda d: json.dumps(d, sort_keys=True))


def _field_diff(cid: str, field: str, legacy: Any, registry: Any) -> str | None:
    if field == "matched_keywords":
        legacy_set = _norm_keywords(legacy)
        registry_set = _norm_keywords(registry)
        if legacy_set != registry_set:
            return (
                f"{cid}.{field}: keywords differ\n"
                f"  legacy-only:  {sorted(legacy_set - registry_set)[:8]}\n"
                f"  registry-only: {sorted(registry_set - legacy_set)[:8]}"
            )
        return None

    if field == "cross_domain_analogies":
        legacy_a = _norm_analogies(legacy)
        registry_a = _norm_analogies(registry)
        if legacy_a != registry_a:
            return f"{cid}.{field}: cross-domain analogies differ"
        return None

    if field == "associated_patterns":
        legacy_set = set(legacy) if isinstance(legacy, list) else set()
        registry_set = set(registry) if isinstance(registry, list) else set()
        if legacy_set != registry_set:
            return f"{cid}.{field}: {legacy_set} != {registry_set}"
        return None

    if str(legacy).strip() != str(registry).strip():
        return f"{cid}.{field}: {legacy!r} != {registry!r}"
    return None


def compare(legacy_path: Path, registry_path: Path) -> tuple[bool, list[str]]:
    legacy_clusters = _load_clusters(legacy_path)
    registry_clusters = _load_clusters(registry_path)

    diffs: list[str] = []

    legacy_ids = set(legacy_clusters)
    registry_ids = set(registry_clusters)
    if legacy_ids != registry_ids:
        only_legacy = sorted(legacy_ids - registry_ids)
        only_registry = sorted(registry_ids - legacy_ids)
        if only_legacy:
            diffs.append(f"patterns missing from registry: {only_legacy}")
        if only_registry:
            diffs.append(f"patterns missing from legacy: {only_registry}")

    for cid in sorted(legacy_ids & registry_ids):
        legacy = legacy_clusters[cid]
        registry = registry_clusters[cid]
        for field in ROUTING_CRITICAL_FIELDS:
            diff = _field_diff(
                cid,
                field,
                legacy.get(field),
                registry.get(field),
            )
            if diff:
                diffs.append(diff)

    return (not diffs), diffs


def compare_code_patterns(
    legacy_dir: Path,
    registry_dir: Path,
    curated_ids: set[str],
) -> tuple[bool, list[str]]:
    diffs: list[str] = []
    legacy_files = {p.name: p for p in legacy_dir.glob("*.md") if p.stem in curated_ids}
    registry_files = {p.name: p for p in registry_dir.glob("*.md") if p.stem in curated_ids}

    legacy_ids = set(legacy_files)
    registry_ids = set(registry_files)
    if legacy_ids != registry_ids:
        only_legacy = sorted(legacy_ids - registry_ids)
        only_registry = sorted(registry_ids - legacy_ids)
        if only_legacy:
            diffs.append(f"code_patterns missing from registry: {only_legacy}")
        if only_registry:
            diffs.append(f"code_patterns missing from legacy: {only_registry}")

    for name in sorted(legacy_ids & registry_ids):
        legacy_text = legacy_files[name].read_text(encoding="utf-8")
        registry_text = registry_files[name].read_text(encoding="utf-8")
        if legacy_text != registry_text:
            diffs.append(f"code_patterns/{name}: content differs")

    return (not diffs), diffs


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare registry-built curated assets to legacy curated assets"
    )
    ap.add_argument(
        "--legacy-bridge",
        type=Path,
        default=ASSETS_DIR / "bridge_index.json",
        help="Path to legacy bridge_index.json",
    )
    ap.add_argument(
        "--registry-assets",
        type=Path,
        required=True,
        help="Path to registry-built asset directory",
    )
    args = ap.parse_args()

    registry_bridge = args.registry_assets / "bridge_index.json"
    legacy_patterns_dir = args.legacy_bridge.parent / "code_patterns"
    registry_patterns_dir = args.registry_assets / "code_patterns"

    print(f"[compare] legacy bridge:   {args.legacy_bridge}")
    print(f"[compare] registry bridge: {registry_bridge}")

    ok_bridge, bridge_diffs = compare(args.legacy_bridge, registry_bridge)
    curated_ids = set(_load_clusters(args.legacy_bridge))
    ok_patterns, pattern_diffs = compare_code_patterns(
        legacy_patterns_dir, registry_patterns_dir, curated_ids
    )

    all_diffs = bridge_diffs + pattern_diffs
    if all_diffs:
        print(f"[compare] {len(all_diffs)} difference(s) found:", file=sys.stderr)
        for d in all_diffs:
            print(f"  • {d}", file=sys.stderr)
        return 1

    n = len(_load_clusters(args.legacy_bridge))
    print(f"[compare] OK — {n} curated patterns are semantically equivalent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
