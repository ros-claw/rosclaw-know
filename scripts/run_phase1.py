#!/usr/bin/env python3
"""Phase 1 runner — full extract→graph→muse pipeline.

Usage:
    python scripts/run_phase1.py --max-pages 200       # first batch
    python scripts/run_phase1.py                       # full run after audit
    python scripts/run_phase1.py --skip-extraction     # rebuild assets only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.pipeline import run_phase1  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Run rosclaw-know Phase 1 pipeline.")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Cap the wiki files processed (default: all).")
    ap.add_argument("--skip-extraction", action="store_true",
                    help="Skip harvester (re-use the SQLite cache).")
    ap.add_argument("--skip-muse", action="store_true",
                    help="Stop after building the graph.")
    ap.add_argument("--skip-curated", action="store_true",
                    help="Skip the curated-pattern publisher step.")
    ap.add_argument("--muse-max-nodes", type=int, default=None,
                    help="Cap how many graph nodes the Muse compiler processes.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    summary = asyncio.run(
        run_phase1(
            max_pages=args.max_pages,
            skip_extraction=args.skip_extraction,
            skip_muse=args.skip_muse,
            skip_curated=args.skip_curated,
            muse_max_nodes=args.muse_max_nodes,
        )
    )
    print("\n=== Phase 1 summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
