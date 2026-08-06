#!/usr/bin/env python3
"""Freeze a reproducible ROSClaw Know-How bundle.

Doc §8 Sprint 4. Captures everything needed to reproduce a routing decision:

  * ``bridge_index.json``          — the routing table at freeze time
  * ``code_patterns/``             — every curated/synth pattern markdown
  * ``routing_panel.yaml``         — the hard-gate panel spec
  * ``routing_panel_result.json``  — live panel verification result
  * ``routing_panel_result.md``    — human-readable panel report
  * ``healthz_snapshot.json``      — HOW /healthz at freeze time
  * ``policy_config.yaml``         — runtime knobs (no secrets)
  * ``know_sha.txt`` / ``how_sha.txt`` — git HEADs
  * ``sha256sum.txt``              — integrity hashes
  * ``bundle_manifest.json``       — top-level summary + cluster counts

Usage::

    python scripts/freeze_bundle.py --label iter5_p0

Refuses to freeze unless:
  * HOW /healthz.status == ok
  * router_backend == seekdb
  * assets_loaded == true
  * routing panel passes 100%
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know import config  # noqa: E402

FROZEN_ROOT = config.DATA_DIR / "frozen"
HOW_ROOT_DEFAULT = config.PROJECT_ROOT.parent / "rosclaw-how"


def _git_head(repo: Path) -> dict[str, Any]:
    """Capture HEAD sha + branch + dirty flag."""
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


def _fetch_health(base: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(
        f"{base.rstrip('/')}/healthz", timeout=timeout
    ) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def _run_panel(
    *,
    base: str,
    api_key: str,
    panel: Path,
    out_json: Path,
    out_markdown: Path,
) -> dict[str, Any]:
    """Run verify_routing_panel.py and return its JSON report."""
    script = config.PROJECT_ROOT / "scripts" / "verify_routing_panel.py"
    cmd = [
        sys.executable,
        str(script),
        "--base",
        base,
        "--panel",
        str(panel),
        "--out",
        str(out_json),
        "--markdown-out",
        str(out_markdown),
        "--strict",
        "--api-key",
        api_key,
    ]
    env = os.environ.copy()
    env["ROSCLAW_HOW_API_KEY"] = api_key
    result = subprocess.run(cmd, cwd=config.PROJECT_ROOT, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            f"verify_routing_panel.py failed with exit code {result.returncode}"
        )
    return json.loads(out_json.read_text(encoding="utf-8"))


def _generate_policy_config(health: dict[str, Any], label: str) -> str:
    cfg = {
        "label": label,
        "frozen_at": datetime.now(UTC).isoformat(),
        "runtime": {
            "router_backend": health.get("router_backend"),
            "similarity_floor": health.get("similarity_floor"),
            "tier_aware_ranking": health.get("tier_aware_ranking"),
            "tier_tiebreak_eps": health.get("tier_tiebreak_eps"),
        },
        "assets": {
            "bridge_index": "bridge_index.json",
            "code_patterns_dir": "code_patterns",
        },
        "panel": {
            "routing_panel": "routing_panel.yaml",
            "routing_panel_result": "routing_panel_result.json",
        },
    }
    return yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)


def freeze(
    label: str,
    how_base: str,
    how_root: Path,
    panel: Path,
    api_key: str,
    policy_config: Path | None,
    notes: str,
    force: bool,
) -> dict[str, Any]:
    if not config.ASSETS_DIR.exists():
        raise SystemExit(f"[freeze] no data/assets/ at {config.ASSETS_DIR}")

    bridge_src = config.ASSETS_DIR / "bridge_index.json"
    if not bridge_src.exists():
        raise SystemExit(f"[freeze] no bridge_index.json at {bridge_src}")

    # ── 0. health gate ───────────────────────────────────────────────────
    try:
        health = _fetch_health(how_base)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[freeze] HOW /healthz HTTP error: {exc.code}")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[freeze] cannot reach {how_base}/healthz: {exc}")

    if health.get("status") != "ok":
        raise SystemExit(
            f"[freeze] HOW status={health.get('status')!r}; refusing to freeze."
        )
    if health.get("router_backend") != "seekdb":
        raise SystemExit(
            f"[freeze] router_backend={health.get('router_backend')!r}; "
            "must be seekdb to freeze."
        )
    if not health.get("assets_loaded"):
        raise SystemExit("[freeze] HOW assets_loaded=False; refusing to freeze.")

    bundle = FROZEN_ROOT / label
    if bundle.exists():
        if not force:
            raise SystemExit(
                f"[freeze] {bundle} already exists. Re-run with --force to overwrite."
            )
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True, exist_ok=True)

    # ── 1. assets ────────────────────────────────────────────────────────
    shutil.copy2(bridge_src, bundle / "bridge_index.json")
    patterns_src = config.CODE_PATTERNS_DIR
    if patterns_src.exists():
        shutil.copytree(patterns_src, bundle / "code_patterns")

    # ── 2. health snapshot ───────────────────────────────────────────────
    (bundle / "healthz_snapshot.json").write_text(
        json.dumps(health, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # ── 3. git commits ───────────────────────────────────────────────────
    know_head = _git_head(config.PROJECT_ROOT)
    how_head = _git_head(how_root)
    (bundle / "know_sha.txt").write_text(
        f"{know_head['sha']} {know_head['branch']}\n", encoding="utf-8"
    )
    (bundle / "how_sha.txt").write_text(
        f"{how_head['sha']} {how_head['branch']}\n", encoding="utf-8"
    )

    # ── 4. routing panel + live result ───────────────────────────────────
    shutil.copy2(panel, bundle / "routing_panel.yaml")
    panel_json = bundle / "routing_panel_result.json"
    panel_md = bundle / "routing_panel_result.md"
    panel_report = _run_panel(
        base=how_base,
        api_key=api_key,
        panel=panel,
        out_json=panel_json,
        out_markdown=panel_md,
    )
    if panel_report["summary"].get("fail", 0) > 0:
        raise SystemExit("[freeze] routing panel has failures; refusing to freeze.")

    # ── 5. policy config ─────────────────────────────────────────────────
    if policy_config and policy_config.exists():
        shutil.copy2(policy_config, bundle / "policy_config.yaml")
    else:
        (bundle / "policy_config.yaml").write_text(
            _generate_policy_config(health, label),
            encoding="utf-8",
        )

    # ── 6. sha256 sums ───────────────────────────────────────────────────
    sha_lines = []
    for f in _walk_bundle(bundle):
        rel = f.relative_to(bundle)
        sha_lines.append(f"{_sha256_file(f)}  {rel}")
    sha_text = "\n".join(sha_lines) + "\n"
    (bundle / "sha256sum.txt").write_text(sha_text, encoding="utf-8")

    # ── 7. manifest ──────────────────────────────────────────────────────
    bridge = json.loads(bridge_src.read_text(encoding="utf-8"))
    clusters = bridge.get("symptom_clusters", {})
    curated = [c for c in clusters.values() if c.get("source") == "curated"]
    synth = [c for c in clusters.values() if c.get("source") != "curated"]
    with_hash = sum(1 for c in clusters.values() if "content_hash" in c)

    manifest = {
        "schema_version": 2,
        "label": label,
        "frozen_at": datetime.now(UTC).isoformat(),
        "notes": notes,
        "know_commit": know_head,
        "how_commit": how_head,
        "how_base": how_base,
        "healthz_status": health.get("status"),
        "router_backend": health.get("router_backend"),
        "cluster_count": len(clusters),
        "curated_count": len(curated),
        "synth_count": len(synth),
        "clusters_with_content_hash": with_hash,
        "panel_total": panel_report["summary"].get("total"),
        "panel_pass": panel_report["summary"].get("pass"),
        "panel_fail": panel_report["summary"].get("fail"),
        "files": [str(f.relative_to(bundle)) for f in _walk_bundle(bundle)],
        "sha256_of_bundle_sha256sum_file": hashlib.sha256(
            sha_text.encode()
        ).hexdigest(),
    }
    (bundle / "bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Re-stamp sha256sums to include the manifest itself.
    sha_lines = []
    for f in _walk_bundle(bundle):
        rel = f.relative_to(bundle)
        sha_lines.append(f"{_sha256_file(f)}  {rel}")
    (bundle / "sha256sum.txt").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True, help="Bundle label (e.g. iter5_p0)")
    ap.add_argument(
        "--how-base",
        default=os.environ.get("ROSCLAW_HOW_BASE", "http://127.0.0.1:8088"),
        help="HOW server base URL.",
    )
    ap.add_argument(
        "--how-root",
        default=str(HOW_ROOT_DEFAULT),
        help="Path to rosclaw-how repository.",
    )
    ap.add_argument(
        "--panel",
        type=Path,
        default=config.PROJECT_ROOT / "data" / "panels" / "routing_panel.yaml",
        help="Routing panel YAML.",
    )
    ap.add_argument(
        "--policy-config",
        type=Path,
        default=None,
        help="Optional policy_config.yaml to copy instead of generating.",
    )
    ap.add_argument(
        "--api-key",
        default=os.environ.get("ROSCLAW_HOW_API_KEY", "rw_sk_dev_local"),
        help="API key for HOW requests.",
    )
    ap.add_argument(
        "--notes", default="", help="Free-form note recorded in the manifest"
    )
    ap.add_argument(
        "--force", action="store_true", help="Overwrite an existing bundle"
    )
    args = ap.parse_args()

    how_root = Path(args.how_root)
    manifest = freeze(
        label=args.label,
        how_base=args.how_base,
        how_root=how_root,
        panel=args.panel,
        api_key=args.api_key,
        policy_config=args.policy_config,
        notes=args.notes,
        force=args.force,
    )
    bundle = FROZEN_ROOT / args.label
    print(f"[freeze] bundle: {bundle}")
    print(f"  files               {len(manifest['files'])}")
    print(f"  clusters            {manifest['cluster_count']} ({manifest['curated_count']} curated)")
    print(f"  content_hash on     {manifest['clusters_with_content_hash']}/{manifest['cluster_count']}")
    print(f"  panel               {manifest['panel_pass']}/{manifest['panel_total']} PASS")
    print(f"  know HEAD           {manifest['know_commit'].get('short_sha', '?')}")
    print(f"  how  HEAD           {manifest['how_commit'].get('short_sha', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
