#!/usr/bin/env python3
"""scripts/autodraft.py — Phase 7 active learning entry point.

Polls rosclaw-how's ``/wiki/v1/blind_spots`` for symptoms that the current
knowledge base can't match, asks DeepSeek to draft a synthetic markdown
covering each gap, then writes the drafts under ``wiki/auto_drafted/``.

Pair with ``scripts/ingest.py`` to actually fold them into bridge_index.
The drafted clusters land with ``priority=0`` (staging) so they don't
short-circuit the operator's review.

Usage:

    .venv/bin/python scripts/autodraft.py
    .venv/bin/python scripts/autodraft.py --url http://127.0.0.1:47820/wiki/v1/blind_spots
    .venv/bin/python scripts/autodraft.py --max-drafts 3 --then-ingest
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.active_learning import (  # noqa: E402
    AUTO_DRAFT_DIR,
    MAX_DRAFTS_PER_RUN,
    autodraft_for_blind_spots,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:47820/wiki/v1/blind_spots")
    ap.add_argument("--max-drafts", type=int, default=MAX_DRAFTS_PER_RUN)
    ap.add_argument("--out-dir", type=Path, default=AUTO_DRAFT_DIR)
    ap.add_argument(
        "--then-ingest", action="store_true",
        help="After drafting, run scripts/ingest.py on the new markdowns.",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    written = asyncio.run(
        autodraft_for_blind_spots(
            url=args.url,
            out_dir=args.out_dir,
            max_drafts=args.max_drafts,
        )
    )
    print(f"drafted: {len(written)}")
    for path in written:
        print(f"  - {path}")

    if not written or not args.then_ingest:
        return 0

    ingest = PROJECT_ROOT / "scripts" / "ingest.py"
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    cmd = [str(venv_py), str(ingest), *[str(p) for p in written]]
    print(f"\nRunning: {' '.join(cmd)}")
    proc = subprocess.run(cmd, timeout=900)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
