#!/usr/bin/env python3
"""Validate curated topic_group/topic_tag coverage in a bridge bundle.

Usage::

    python scripts/validate_topic_coverage.py \
        --bridge data/assets/bridge_index.json

Exit code 0 when curated coverage is 100%, non-zero otherwise. Prints a JSON
report to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _is_curated(cluster: dict) -> bool:
    tier = (cluster.get("source_tier") or "").upper()
    legacy_source = (cluster.get("source") or "").lower()
    return tier.startswith("S_") or tier.startswith("A_") or legacy_source == "curated"


def validate_topic_coverage(data: dict) -> dict:
    clusters = data.get("symptom_clusters") or {}
    curated_total = 0
    curated_covered = 0
    missing: list[str] = []

    for cluster_id, raw in clusters.items():
        if not isinstance(raw, dict):
            continue
        if not _is_curated(raw):
            continue
        curated_total += 1
        has_group = isinstance(raw.get("topic_group"), str) and bool(raw["topic_group"].strip())
        has_tag = isinstance(raw.get("topic_tag"), str) and bool(raw["topic_tag"].strip())
        if has_group and has_tag:
            curated_covered += 1
        else:
            missing.append(
                {
                    "cluster_id": cluster_id,
                    "topic_group": raw.get("topic_group"),
                    "topic_tag": raw.get("topic_tag"),
                }
            )

    coverage = f"{curated_covered}/{curated_total}" if curated_total else "0/0"
    ok = curated_total > 0 and curated_covered == curated_total

    return {
        "ok": ok,
        "curated_total": curated_total,
        "curated_with_topic_group": sum(
            1 for m in missing if isinstance(m["topic_group"], str) and m["topic_group"].strip()
        )
        + curated_covered,
        "curated_with_topic_tag": sum(
            1 for m in missing if isinstance(m["topic_tag"], str) and m["topic_tag"].strip()
        )
        + curated_covered,
        "coverage": coverage,
        "missing": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate curated topic coverage")
    parser.add_argument(
        "--bridge",
        type=Path,
        default=PROJECT_ROOT / "data" / "assets" / "bridge_index.json",
        help="Path to bridge_index.json",
    )
    args = parser.parse_args()

    if not args.bridge.exists():
        print(json.dumps({"ok": False, "error": f"bridge not found: {args.bridge}"}))
        return 1

    data = json.loads(args.bridge.read_text(encoding="utf-8"))
    report = validate_topic_coverage(data)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
