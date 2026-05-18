#!/usr/bin/env python3
"""scripts/ingest.py — incrementally add new wiki content to rosclaw-know.

Usage:

    .venv/bin/python scripts/ingest.py path/to/new_paper.md
    .venv/bin/python scripts/ingest.py path/to/dir/of/papers/
    .venv/bin/python scripts/ingest.py file1.md file2.md --dry-run

Files that are already in the manifest with the same SHA-256 are skipped.
The harvester runs only on dirty (new / changed) files; the weaver
rebuilds the full graph from extracted_pages; Muse runs only on graph
nodes that don't already exist in ``bridge_index.json``. Existing
clusters (and their Phase 4 feedback fields like ``priority`` / ``uplift_mean``)
are preserved.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.incremental_pipeline import run_incremental_ingest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "paths", nargs="+", type=Path,
        help="Markdown files or directories to ingest.",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen without running harvester / Muse.",
    )
    ap.add_argument(
        "--manifest", type=Path, default=None,
        help="Override source_manifest.json path (default: data/source_manifest.json)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    try:
        summary = asyncio.run(
            run_incremental_ingest(
                args.paths,
                manifest_path=args.manifest,
                dry_run=args.dry_run,
            )
        )
    except RuntimeError as exc:
        print(f"ingest aborted: {exc}", file=sys.stderr)
        return 2

    print()
    print("--- ingest summary ---")
    print(f"candidates discovered: {summary['candidates_total']}")
    print(f"dirty (new/changed):   {summary['dirty_total']}")
    if args.dry_run:
        for path, status in summary.get("dirty_files", []):
            print(f"  {status:9s} {path}")
        return 0

    print(f"graph nodes:           {summary.get('graph_nodes')}")
    print(f"graph edges:           {summary.get('graph_edges')}")
    print(f"new graph nodes:       {summary.get('new_graph_nodes')}")
    muse = summary.get("muse", {})
    print(f"new clusters minted:   {muse.get('new_clusters_minted', 0)}")
    merge = summary.get("bridge_merge", {})
    print(
        f"bridge merge:          +{merge.get('added', 0)} added, "
        f"{merge.get('skipped_existing', 0)} skipped, "
        f"total {merge.get('total', 0)}"
    )
    print(f"manifest:              {summary.get('manifest_path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
