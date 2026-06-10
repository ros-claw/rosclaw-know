#!/usr/bin/env python3
"""Freeze the live bridge_index + code_patterns + eval panel + commits into
an immutable bundle under ``data/frozen/<label>/``.

docs/know-how下一步建议.md §4.2 — every serious A/B from now on must launch
from a frozen bundle, NOT from the live ``data/assets/`` symlink. The bundle
captures:

  * ``bridge_index.json``    — the routing table at freeze time
  * ``code_patterns/``       — every curated/synth pattern markdown
  * ``routing_canary.json``  — the self-probe spec (used by how on reload)
  * ``how_commit.txt``       — rosclaw-how git HEAD (sha + branch + dirty?)
  * ``know_commit.txt``      — rosclaw-know git HEAD
  * ``eval_panel.yaml``      — the 18-task Frontier-Eng panel snapshot
  * ``model_config.yaml``    — endpoint host (no key), model name, embedder
  * ``bundle_manifest.json`` — top-level summary + cluster counts + hash
  * ``sha256sum.txt``        — sha256 of every file in the bundle

Usage::

    python scripts/freeze_bundle.py --label iter4_d07ddac
    python scripts/freeze_bundle.py --label iter5_audit  --notes "P0 baseline"

The script refuses to overwrite an existing bundle. Pass ``--force`` to nuke
and rebuild a label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know import config  # noqa: E402

FROZEN_ROOT = config.DATA_DIR / "frozen"
HOW_ROOT_DEFAULT = config.PROJECT_ROOT.parent / "rosclaw-how"


def _git_head(repo: Path) -> dict[str, str]:
    """Capture HEAD sha + branch + dirty flag (or '(not a git repo)')."""
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"], text=True
        ).strip()
        return {
            "repo": str(repo),
            "sha": sha,
            "short_sha": sha[:8],
            "branch": branch,
            "dirty": bool(dirty),
            "porcelain": dirty if dirty else "",
        }
    except subprocess.CalledProcessError as exc:
        return {"repo": str(repo), "error": str(exc)}
    except FileNotFoundError:
        return {"repo": str(repo), "error": "git missing"}


def _snapshot_frontier_panel(know_root: Path) -> list[str]:
    """Pull task_id values from verify_frontier_eng.py.

    Source-of-truth lives inline in the verifier script, so we treat that
    file as canonical and just grep the IDs out.
    """
    vf = know_root / "scripts" / "verify_frontier_eng.py"
    if not vf.exists():
        return []
    text = vf.read_text(encoding="utf-8")
    return re.findall(r'"task_id":\s*"(TASK_[A-Za-z0-9_]+)"', text)


def _model_config() -> dict[str, Any]:
    """Capture model identity WITHOUT the API key."""
    host = config.DEEPSEEK_BASE_URL
    return {
        "deepseek_base_url": host,
        "deepseek_extractor_model": config.DEEPSEEK_EXTRACTOR_MODEL,
        "deepseek_muse_model": config.DEEPSEEK_MUSE_MODEL,
        "embedding_model": config.EMBEDDING_MODEL,
        "mock_llm": config.MOCK_LLM,
        "seekdb_host_set": bool(config.SEEKDB_HOST),
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_bundle(bundle: Path):
    """Yield every regular file in the bundle, deterministically ordered."""
    for p in sorted(bundle.rglob("*")):
        if p.is_file() and p.name != "sha256sum.txt":
            yield p


def freeze(label: str, how_root: Path, notes: str, force: bool) -> dict[str, Any]:
    if not config.ASSETS_DIR.exists():
        raise SystemExit(f"[freeze] no data/assets/ at {config.ASSETS_DIR}")

    bridge_src = config.ASSETS_DIR / "bridge_index.json"
    if not bridge_src.exists():
        raise SystemExit(f"[freeze] no bridge_index.json at {bridge_src}")

    bundle = FROZEN_ROOT / label
    if bundle.exists():
        if not force:
            raise SystemExit(
                f"[freeze] {bundle} already exists. Re-run with --force to overwrite."
            )
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True, exist_ok=True)

    # 1. bridge + patterns
    shutil.copy2(bridge_src, bundle / "bridge_index.json")
    patterns_src = config.CODE_PATTERNS_DIR
    if patterns_src.exists():
        shutil.copytree(patterns_src, bundle / "code_patterns")

    # 2. routing canary (build it if missing)
    canary_src = config.ASSETS_DIR / "routing_canary.json"
    if not canary_src.exists():
        print(f"[freeze] {canary_src} missing — running build_routing_canary.py first")
        subprocess.check_call(
            [sys.executable, str(config.PROJECT_ROOT / "scripts" / "build_routing_canary.py")]
        )
    if canary_src.exists():
        shutil.copy2(canary_src, bundle / "routing_canary.json")

    # 3. git commits
    know_head = _git_head(config.PROJECT_ROOT)
    how_head = _git_head(how_root)
    (bundle / "know_commit.txt").write_text(
        json.dumps(know_head, indent=2) + "\n", encoding="utf-8"
    )
    (bundle / "how_commit.txt").write_text(
        json.dumps(how_head, indent=2) + "\n", encoding="utf-8"
    )

    # 4. eval panel
    tasks = _snapshot_frontier_panel(config.PROJECT_ROOT)
    home = [t for t in tasks if not t.startswith("TASK_W_")]
    wild = [t for t in tasks if t.startswith("TASK_W_")]
    panel = {
        "schema_version": 1,
        "panel_name": "frontier_eng_18",
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_turf_count": len(home),
        "wild_count": len(wild),
        "home_turf": home,
        "wild": wild,
        "source_of_truth": "scripts/verify_frontier_eng.py @ rosclaw-know",
    }
    (bundle / "eval_panel.yaml").write_text(
        json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 5. model config (no secrets)
    (bundle / "model_config.yaml").write_text(
        json.dumps(_model_config(), indent=2), encoding="utf-8"
    )

    # 6. cluster counters for manifest
    with open(bridge_src, encoding="utf-8") as fh:
        bridge = json.load(fh)
    clusters = bridge.get("symptom_clusters", {})
    curated = [c for c in clusters.values() if c.get("source") == "curated"]
    with_hash = sum(1 for c in clusters.values() if "content_hash" in c)

    # 7. sha256sums (after all files written)
    sha_lines = []
    for f in _walk_bundle(bundle):
        rel = f.relative_to(bundle)
        sha_lines.append(f"{_sha256_file(f)}  {rel}")
    sha_text = "\n".join(sha_lines) + "\n"
    (bundle / "sha256sum.txt").write_text(sha_text, encoding="utf-8")

    # 8. manifest
    manifest = {
        "schema_version": 1,
        "label": label,
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": notes,
        "know_commit": know_head,
        "how_commit": how_head,
        "cluster_count": len(clusters),
        "curated_count": len(curated),
        "synth_count": len(clusters) - len(curated),
        "clusters_with_content_hash": with_hash,
        "panel_home_count": len(home),
        "panel_wild_count": len(wild),
        "files": [str(f.relative_to(bundle)) for f in _walk_bundle(bundle)],
        "sha256_of_bundle_sha256sum_file": hashlib.sha256(sha_text.encode()).hexdigest(),
    }
    (bundle / "bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Re-stamp sha256sums to include the manifest itself.
    sha_lines = []
    for f in _walk_bundle(bundle):
        rel = f.relative_to(bundle)
        sha_lines.append(f"{_sha256_file(f)}  {rel}")
    (bundle / "sha256sum.txt").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="Bundle label (e.g. iter4_d07ddac)")
    ap.add_argument("--how-root", default=str(HOW_ROOT_DEFAULT))
    ap.add_argument("--notes", default="", help="Free-form note recorded in the manifest")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing bundle of this label")
    args = ap.parse_args()

    how_root = Path(args.how_root)
    manifest = freeze(args.label, how_root, args.notes, args.force)
    bundle = FROZEN_ROOT / args.label
    print(f"[freeze] bundle: {bundle}")
    print(f"  files               {len(manifest['files'])}")
    print(f"  clusters            {manifest['cluster_count']} ({manifest['curated_count']} curated)")
    print(f"  content_hash on     {manifest['clusters_with_content_hash']}/{manifest['cluster_count']}")
    print(f"  home_turf           {manifest['panel_home_count']}")
    print(f"  wild                {manifest['panel_wild_count']}")
    print(f"  know HEAD           {manifest['know_commit'].get('short_sha', '?')}")
    print(f"  how  HEAD           {manifest['how_commit'].get('short_sha', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
