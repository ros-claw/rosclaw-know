#!/usr/bin/env python3
"""Publish know's assets to a rosclaw-how project tree.

By default it symlinks ``data/assets/`` from know into ``data/assets/`` in
how — so every fresh Muse run is picked up by how's next restart without
copying gigabytes. Pass ``--mode copy`` for a real rsync-style copy when
you need an immutable snapshot (e.g. for production deploys).

Usage::

    # Local development — symlink (default, instant)
    python scripts/publish_to_how.py

    # CI / production snapshot — hard copy
    python scripts/publish_to_how.py --mode copy

    # Custom how location
    python scripts/publish_to_how.py --how-root /srv/rosclaw-how

After publishing, restart the how server so its asset_loader re-ingests
into SeekDB::

    curl -X POST http://localhost:8080/admin/reload     # if exposed
    # or simply restart the process
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.config import ASSETS_DIR, PROJECT_ROOT  # noqa: E402

DEFAULT_HOW_ROOT = PROJECT_ROOT.parent / "rosclaw-how"


def _is_inside(path: Path, parent: Path) -> bool:
    """Strictly inside — equal paths are NOT considered inside."""
    try:
        rel = path.relative_to(parent)
    except ValueError:
        return False
    return rel != Path(".")


def publish_symlink(how_assets_dir: Path, source_assets: Path) -> None:
    """Symlink how's data/assets/ → know's data/assets/."""
    how_assets_dir.parent.mkdir(parents=True, exist_ok=True)
    if how_assets_dir.is_symlink() or how_assets_dir.exists():
        if how_assets_dir.is_symlink():
            current = how_assets_dir.readlink()
            if current.resolve() == source_assets.resolve():
                print(f"[noop] already symlinked: {how_assets_dir} → {current}")
                return
            how_assets_dir.unlink()
        else:
            # An existing real directory — back it up before replacing.
            backup = how_assets_dir.with_suffix(".prev")
            if backup.exists():
                shutil.rmtree(backup)
            how_assets_dir.rename(backup)
            print(f"[backup] moved existing dir to {backup}")
    how_assets_dir.symlink_to(source_assets.resolve())
    print(f"[symlink] {how_assets_dir} → {source_assets.resolve()}")


def publish_copy(how_assets_dir: Path, source_assets: Path) -> None:
    """Snapshot-copy how's data/assets/."""
    how_assets_dir.parent.mkdir(parents=True, exist_ok=True)
    if how_assets_dir.is_symlink():
        how_assets_dir.unlink()
    if how_assets_dir.exists():
        shutil.rmtree(how_assets_dir)
    shutil.copytree(source_assets, how_assets_dir)
    print(f"[copy] {how_assets_dir} ← {source_assets}")


def _summarise(assets_dir: Path) -> None:
    bridge_path = assets_dir / "bridge_index.json"
    patterns_dir = assets_dir / "code_patterns"
    if not bridge_path.exists():
        print(f"[warn] bridge_index.json missing under {assets_dir}")
        return
    with open(bridge_path, encoding="utf-8") as fh:
        data = json.load(fh)
    n_clusters = len(data.get("symptom_clusters", {}))
    safety = data.get("safety_label_index", {})
    n_curated = sum(1 for v in data.get("symptom_clusters", {}).values() if v.get("source") == "curated")
    n_patterns = len(list(patterns_dir.glob("*.md"))) if patterns_dir.exists() else 0
    print()
    print(f"  bridge_index.json   {bridge_path}")
    print(f"  symptom_clusters    {n_clusters} ({n_curated} curated, {n_clusters - n_curated} Muse-mined)")
    print(f"  safety_label_index  {len(safety)} ({', '.join(safety) if safety else '—'})")
    print(f"  code_patterns/      {n_patterns} files")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--how-root",
        type=Path,
        default=DEFAULT_HOW_ROOT,
        help=f"Path to rosclaw-how project root (default: {DEFAULT_HOW_ROOT}).",
    )
    ap.add_argument(
        "--mode",
        choices=("symlink", "copy"),
        default="symlink",
        help="symlink (instant, dev) or copy (snapshot, CI).",
    )
    args = ap.parse_args()

    source = ASSETS_DIR
    if not source.exists():
        print(f"[error] source not found: {source}. Run scripts/run_phase1.py first.", file=sys.stderr)
        return 1

    how_root: Path = args.how_root.resolve()
    if not how_root.exists():
        print(f"[error] rosclaw-how root not found: {how_root}", file=sys.stderr)
        return 1

    target = how_root / "data" / "assets"

    # Refuse to publish into our own tree.
    if _is_inside(target.resolve(), source.resolve()):
        print(f"[error] target {target} is inside source {source}; refusing.", file=sys.stderr)
        return 1

    if args.mode == "symlink":
        publish_symlink(target, source)
    else:
        publish_copy(target, source)

    _summarise(target)
    print()
    print("Next: restart rosclaw-how so its startup loader re-ingests into SeekDB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
