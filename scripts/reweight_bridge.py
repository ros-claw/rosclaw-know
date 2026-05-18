#!/usr/bin/env python3
"""scripts/reweight_bridge.py — apply pattern_metrics.json to bridge_index.json.

Runs after :doc:`distill_feedback` has produced per-pattern metrics. The
output is a mutation of ``data/assets/bridge_index.json`` (only when at
least one cluster value actually changes — idempotent on repeat runs).

Usage:

    .venv/bin/python scripts/reweight_bridge.py
    .venv/bin/python scripts/reweight_bridge.py --bridge custom_bridge.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.bridge_reweighter import reweight_bridge_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bridge", type=Path, default=None,
                    help="Path to bridge_index.json (default: data/assets/bridge_index.json)")
    ap.add_argument("--metrics", type=Path, default=None,
                    help="Path to pattern_metrics.json (default: data/assets/pattern_metrics.json)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    stats = reweight_bridge_index(bridge_path=args.bridge, metrics_path=args.metrics)
    print(
        f"clusters={stats['clusters_total']}  "
        f"touched={stats['clusters_touched']}  "
        f"demoted={stats['clusters_demoted']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
