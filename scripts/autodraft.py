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
import os
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

log = logging.getLogger("rosclaw_know.autodraft")


def _find_rosclaw_how_root() -> Path | None:
    """Locate the sibling rosclaw-how repo for the topic_group inference step.

    Resolution order:
      1. ``ROSCLAW_HOW_PATH`` env var, if set and pointing at a directory
         that has ``scripts/infer_autodraft_topic_group.py``.
      2. ``../rosclaw-how`` relative to this project (the standard sibling
         checkout that the rosclaw-wiki workspace uses).

    Returns None if neither resolves — caller logs and skips the step.
    """
    candidates: list[Path] = []
    env = os.environ.get("ROSCLAW_HOW_PATH")
    if env:
        candidates.append(Path(env).expanduser().resolve())
    candidates.append((PROJECT_ROOT.parent / "rosclaw-how").resolve())

    for c in candidates:
        if (c / "scripts" / "infer_autodraft_topic_group.py").exists():
            return c
    return None


def _label_new_clusters_via_how(how_root: Path) -> int:
    """Subprocess into rosclaw-how's venv to infer topic_group/topic_tag
    on clusters that the ingest step just minted (anything without a
    ``topic_group`` field). Returns the script's exit code.

    The work happens in rosclaw-how because that's where the sentence-
    transformer model and the fingerprint code already live; adding a
    second copy in rosclaw-know would duplicate ~120 MB of model weights
    and the inference code. The two repos already share bridge_index.json
    via the data/assets hardlink, so the inference writes through to the
    same file rosclaw-know just updated.

    No-op-safe: if every cluster already has topic_group, the script
    prints ``updated=0 ...`` and returns 0.
    """
    venv_py = how_root / ".venv" / "bin" / "python"
    if not venv_py.exists():
        log.warning("rosclaw-how venv not found at %s; skipping topic_group inference.", venv_py)
        return 1
    script = how_root / "scripts" / "infer_autodraft_topic_group.py"
    print(f"\nRunning: {venv_py} {script}  (auto-label new clusters)")
    proc = subprocess.run([str(venv_py), str(script)], timeout=300)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:47820/wiki/v1/blind_spots")
    ap.add_argument("--max-drafts", type=int, default=MAX_DRAFTS_PER_RUN)
    ap.add_argument("--out-dir", type=Path, default=AUTO_DRAFT_DIR)
    ap.add_argument(
        "--then-ingest", action="store_true",
        help="After drafting, run scripts/ingest.py on the new markdowns.",
    )
    ap.add_argument(
        "--skip-topic-group",
        action="store_true",
        help=(
            "Skip the post-ingest topic_group/topic_tag inference step. "
            "Without inference, freshly ingested clusters land without "
            "topic_group and are invisible to rosclaw-how's CATALYST "
            "filter — only use this flag for offline runs."
        ),
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
    if proc.returncode != 0:
        return proc.returncode

    if args.skip_topic_group:
        log.info("--skip-topic-group set; not auto-labeling new clusters.")
        return 0

    how_root = _find_rosclaw_how_root()
    if how_root is None:
        log.warning(
            "rosclaw-how not located (set ROSCLAW_HOW_PATH or place at "
            "../rosclaw-how); skipping topic_group inference. New clusters "
            "will land without topic_group and stay invisible to CATALYST.",
        )
        return 0

    rc = _label_new_clusters_via_how(how_root)
    if rc != 0:
        log.warning("topic_group inference exited with code %d; continuing.", rc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
