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


def _validate_topic_coverage(assets_dir: Path) -> tuple[bool, list[str]]:
    """Refuse publish when curated clusters violate topic_group/topic_tag
    coverage rules (doc §7.1).

    Rules:
      - source == "curated"  → MUST have BOTH topic_group AND topic_tag set
      - topic_group set, topic_tag empty → ERROR (HOW's _build_group_to_
        fingerprint_text silently drops these from fingerprints — the
        iter4_p3 → iter4_p4 incident root cause)
      - topic_tag set, topic_group empty → ERROR (symmetric: tag without
        group has no fingerprint to join)

    Returns (ok, problems). ok=True when the bridge passes.
    """
    bridge_path = assets_dir / "bridge_index.json"
    if not bridge_path.exists():
        return False, [f"bridge_index.json missing under {assets_dir}"]
    with open(bridge_path, encoding="utf-8") as fh:
        bridge = json.load(fh)
    clusters = bridge.get("symptom_clusters", {}) or {}
    problems: list[str] = []
    for cluster_id, info in clusters.items():
        if not isinstance(info, dict):
            continue
        source = info.get("source")
        topic_group = info.get("topic_group")
        topic_tag = info.get("topic_tag")

        if source == "curated":
            if not topic_group:
                problems.append(
                    f"curated cluster {cluster_id!r}: missing topic_group"
                )
            if not topic_tag:
                problems.append(
                    f"curated cluster {cluster_id!r}: missing topic_tag"
                )
        # Both-or-none invariant applies to all clusters: a topic_group
        # without topic_tag is dead-code on HOW's fingerprint compute.
        if topic_group and not topic_tag:
            problems.append(
                f"cluster {cluster_id!r}: has topic_group={topic_group!r} "
                "but topic_tag is empty (silently drops from HOW "
                "fingerprint — see iter4_p3 → iter4_p4 incident)"
            )
        if topic_tag and not topic_group:
            problems.append(
                f"cluster {cluster_id!r}: has topic_tag={topic_tag!r} "
                "but topic_group is empty (orphaned tag, no group to join)"
            )
    return (not problems), problems


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
    ap.add_argument(
        "--skip-validate",
        action="store_true",
        help=(
            "Skip the topic_group/topic_tag coverage pre-flight gate. "
            "Use ONLY for emergency hotfixes — silently-dropped curated "
            "clusters cost us 2 iterations to find last time (iter4_p3 "
            "→ iter4_p4). See doc §7.1."
        ),
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

    # Doc §7.1 P0 pre-flight gate — curated coverage MUST be 100% or
    # publish fails. iter4_p3 shipped curated without topic_tag and
    # HOW silently dropped them from fingerprint compute; the next
    # paired_ab arc lost 1 iteration before iter4_p4 caught it.
    if not args.skip_validate:
        ok, problems = _validate_topic_coverage(source)
        if not ok:
            print("[error] topic_coverage validation FAILED — refusing to publish:", file=sys.stderr)
            for p in problems:
                print(f"  • {p}", file=sys.stderr)
            print(
                "  (override with --skip-validate ONLY for emergency hotfixes)",
                file=sys.stderr,
            )
            return 1
        print("[validate] topic_group/topic_tag coverage OK")

    if args.mode == "symlink":
        publish_symlink(target, source)
    else:
        publish_copy(target, source)

    _summarise(target)
    print()
    print("Next: restart rosclaw-how so its startup loader re-ingests into SeekDB.")
    print(
        "Then: python scripts/verify_routing_panel.py --strict "
        "(doc §6 hard gate before any paired_ab)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
