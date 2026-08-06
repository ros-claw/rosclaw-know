#!/usr/bin/env python3
"""Validate a ``bridge_index.json`` against Bridge Schema v2.

Usage::

    python scripts/validate_bridge_schema.py \
        --bridge data/assets/bridge_index.json \
        --code-patterns data/assets/code_patterns

Exit code 0 when the bundle is valid, 1 otherwise. Prints a JSON report to
stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.bridge_schema import validate_bridge_index  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate bridge_index.json schema")
    parser.add_argument(
        "--bridge",
        type=Path,
        default=PROJECT_ROOT / "data" / "assets" / "bridge_index.json",
        help="Path to bridge_index.json",
    )
    parser.add_argument(
        "--code-patterns",
        type=Path,
        default=PROJECT_ROOT / "data" / "assets" / "code_patterns",
        help="Path to code_patterns directory",
    )
    args = parser.parse_args()

    if not args.bridge.exists():
        print(json.dumps({"ok": False, "errors": [f"bridge not found: {args.bridge}"], "warnings": []}))
        return 1

    data = json.loads(args.bridge.read_text(encoding="utf-8"))
    report = validate_bridge_index(data, code_patterns_dir=args.code_patterns)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
