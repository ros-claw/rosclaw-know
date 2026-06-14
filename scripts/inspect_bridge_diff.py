#!/usr/bin/env python3
"""Inspect differences between two bridge_index.json bundles.

Usage::

    python scripts/inspect_bridge_diff.py \
        --before data/frozen/iter4_p9/bridge_index.json \
        --after data/assets/bridge_index.json

Exit code 0 always (this is a read-only diagnostic). Prints a JSON summary of
added, removed, changed, and unchanged clusters. A cluster is considered
"changed" when its ``content_hash`` or ``metadata_hash`` differs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_clusters(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    clusters = data.get("symptom_clusters") or {}
    return {str(k): v for k, v in clusters.items() if isinstance(v, dict)}


def hash_tuple(cluster: dict) -> tuple[str, str]:
    return (
        str(cluster.get("content_hash") or ""),
        str(cluster.get("metadata_hash") or ""),
    )


def inspect_diff(before_path: Path, after_path: Path) -> dict:
    before = load_clusters(before_path)
    after = load_clusters(after_path)

    before_ids = set(before)
    after_ids = set(after)

    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    common = sorted(before_ids & after_ids)

    changed = []
    unchanged = []
    for cid in common:
        if hash_tuple(before[cid]) != hash_tuple(after[cid]):
            changed.append(
                {
                    "cluster_id": cid,
                    "before": {
                        "content_hash": before[cid].get("content_hash"),
                        "metadata_hash": before[cid].get("metadata_hash"),
                    },
                    "after": {
                        "content_hash": after[cid].get("content_hash"),
                        "metadata_hash": after[cid].get("metadata_hash"),
                    },
                }
            )
        else:
            unchanged.append(cid)

    return {
        "before_path": str(before_path),
        "after_path": str(after_path),
        "before_cluster_count": len(before),
        "after_cluster_count": len(after),
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff two bridge_index.json files")
    parser.add_argument("--before", type=Path, required=True, help="Older bridge_index.json")
    parser.add_argument("--after", type=Path, required=True, help="Newer bridge_index.json")
    args = parser.parse_args()

    if not args.before.exists():
        print(json.dumps({"error": f"--before not found: {args.before}"}))
        return 1
    if not args.after.exists():
        print(json.dumps({"error": f"--after not found: {args.after}"}))
        return 1

    report = inspect_diff(args.before, args.after)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
