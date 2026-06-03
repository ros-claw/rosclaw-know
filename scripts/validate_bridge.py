#!/usr/bin/env python3
"""Validate `data/assets/bridge_index.json` against the v2 schema.

Wraps :func:`rosclaw_know.schemas.validate_bridge` so it can be run from
CI / pre-deploy hooks.  Exits non-zero on any structural problem so it
can gate merges.

Usage::

    python scripts/validate_bridge.py
    python scripts/validate_bridge.py path/to/bridge_index.json
    python scripts/validate_bridge.py --quiet         # only fail-cases printed
    python scripts/validate_bridge.py --report json   # machine-readable report

The "fail-loud schema" the v1.5 plan §11.1 asks for: any unknown field,
bad domain, illegal priority, or malformed nested structure raises
``pydantic.ValidationError`` and we surface it.

Designed to be cheap (<1 s on a 349-cluster bridge) so it can run on
every push.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from rosclaw_know import config
from rosclaw_know.schemas import BridgeIndexV2, validate_bridge

logger = logging.getLogger("validate_bridge")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validate bridge_index.json against the v2 schema."
    )
    p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to bridge_index.json (default: data/assets/bridge_index.json).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Only print on failure; suppress the summary on success.",
    )
    p.add_argument(
        "--report",
        choices=["text", "json"],
        default="text",
        help="Output format on success (default: text).",
    )
    return p.parse_args(argv)


def _summarize(bi: BridgeIndexV2) -> dict[str, int]:
    """Cheap counters useful for ops dashboards."""
    summary: dict[str, int] = {
        "clusters_total": len(bi.symptom_clusters),
        "safety_labels": len(bi.safety_label_index),
        "with_metadata": 0,
        "priority_staging": 0,
        "priority_production": 0,
        "priority_demoted": 0,
        "priority_legacy": 0,
    }
    for c in bi.symptom_clusters.values():
        if c.metadata is not None:
            summary["with_metadata"] += 1
        match c.priority:
            case 0:
                summary["priority_staging"] += 1
            case 1:
                summary["priority_production"] += 1
            case -1:
                summary["priority_demoted"] += 1
            case None:
                summary["priority_legacy"] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    path = Path(args.path) if args.path else (config.ASSETS_DIR / "bridge_index.json")
    if not path.exists():
        print(f"ERROR: bridge_index not found at {path}", file=sys.stderr)
        return 2

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: bridge_index is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        bi = validate_bridge(raw)
    except ValidationError as exc:
        print("ERROR: bridge_index v2 schema validation FAILED.", file=sys.stderr)
        print(f"  path: {path}", file=sys.stderr)
        # Print compact error summary (one line per violation).
        for err in exc.errors():
            loc = ".".join(str(s) for s in err.get("loc", []))
            msg = err.get("msg", "")
            print(f"  - {loc}: {msg}", file=sys.stderr)
        return 1

    summary = _summarize(bi)
    if args.report == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print(f"OK  bridge_index v2 valid at {path}")
        for k, v in summary.items():
            print(f"  {k:>22}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
