#!/usr/bin/env python3
"""scripts/ingest_awesome.py — Phase 8 corpus injection from awesome lists.

Pulls a curated GitHub awesome-list, downloads each referenced
repo/blog/PDF, writes per-entry markdown into ``wiki/awesome_corpus/<slug>/``
with priority=0 staging frontmatter, then optionally chains
``scripts/ingest.py`` to extract → weave → Muse the new corpus.

Usage:

    .venv/bin/python scripts/ingest_awesome.py \
        --url https://github.com/A-make/awesome-control-theory \
        --limit 30

    .venv/bin/python scripts/ingest_awesome.py \
        --url https://github.com/hslatman/awesome-industrial-control-system-security \
        --section "papers" --section "tools" --limit 25 --then-ingest
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.awesome_fetcher import (  # noqa: E402
    DEFAULT_OUT_DIR,
    fetch_awesome_list,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--url", action="append", required=True,
        help="Awesome-list GitHub URL (may be passed multiple times).",
    )
    ap.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR,
        help=f"Corpus root (default: {DEFAULT_OUT_DIR}).",
    )
    ap.add_argument(
        "--limit", type=int, default=None,
        help="Cap entries downloaded per list (omit = all).",
    )
    ap.add_argument(
        "--section", action="append", default=None,
        help="Only ingest entries under sections containing this substring (case-insensitive). Repeatable.",
    )
    ap.add_argument(
        "--per-fetch-sleep", type=float, default=0.4,
        help="Politeness delay between HTTP calls.",
    )
    ap.add_argument(
        "--then-ingest", action="store_true",
        help="After fetching, run scripts/ingest.py on the new corpus.",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    all_written: list[Path] = []
    for url in args.url:
        results = fetch_awesome_list(
            url,
            out_dir=args.out_dir,
            limit=args.limit,
            per_fetch_sleep=args.per_fetch_sleep,
            sections_filter=args.section,
        )
        all_written.extend(r.path for r in results if r.path is not None)

    print()
    print(f"=== awesome ingest summary ===")
    print(f"lists processed:   {len(args.url)}")
    print(f"corpus files written: {len(all_written)}")
    if all_written:
        for p in all_written[:5]:
            print(f"  - {p}")
        if len(all_written) > 5:
            print(f"  ... (+{len(all_written) - 5} more)")

    if not args.then_ingest or not all_written:
        return 0

    print("\nChaining scripts/ingest.py on the new corpus ...")
    ingest = PROJECT_ROOT / "scripts" / "ingest.py"
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    # Pass per-list subdirectories rather than every file (let ingest.py recurse).
    list_dirs = sorted({p.parent for p in all_written})
    cmd = [str(venv_py), str(ingest), *[str(d) for d in list_dirs]]
    proc = subprocess.run(cmd, timeout=2400)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
