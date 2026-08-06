#!/usr/bin/env python3
"""scripts/lint_bridge.py — find anomalies in bridge_index.json + code_patterns/.

Phase 5 health checker. Reports:

  * Orphan code_patterns:    *.md files no cluster references
  * Missing pattern files:   cluster references missing .md files
  * Duplicate standard_name: clusters with identical symptom (semantic dup)
  * Stale demotions:         priority=-1 clusters with no positive feedback
                             for more than ``--stale-days`` days

Exit code 0 = clean, 1 = anomalies found. Suitable for CI gates.

Usage:

    .venv/bin/python scripts/lint_bridge.py
    .venv/bin/python scripts/lint_bridge.py --json out.json --stale-days 30
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import ASSETS_DIR, CODE_PATTERNS_DIR  # noqa: E402

logger = logging.getLogger("rosclaw_know.lint_bridge")


def _load_bridge(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.warning("bridge_index.json not found at %s", path)
        return {"symptom_clusters": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("bridge_index.json is invalid JSON: %s", exc)
        return {"symptom_clusters": {}}


def find_orphan_patterns(bridge: dict[str, Any], patterns_dir: Path) -> list[str]:
    """code_patterns/*.md files that no cluster ``associated_patterns`` references."""
    referenced: set[str] = set()
    for cluster in bridge.get("symptom_clusters", {}).values():
        for pid in cluster.get("associated_patterns", []) or []:
            referenced.add(str(pid))
    orphans: list[str] = []
    if not patterns_dir.exists():
        return orphans
    for fp in sorted(patterns_dir.iterdir()):
        if not fp.is_file() or fp.suffix != ".md":
            continue
        stem = fp.stem
        # Patterns are referenced by either bare stem ("anti_windup_pid") or
        # the prefixed form Muse uses ("pattern_<slug>"). Either form counts.
        if stem in referenced:
            continue
        if f"pattern_{stem}" in referenced:
            continue
        orphans.append(fp.name)
    return orphans


def find_missing_pattern_files(
    bridge: dict[str, Any], patterns_dir: Path
) -> list[tuple[str, str]]:
    """``[(cluster_id, missing_pattern_id), ...]`` for dangling references."""
    if not patterns_dir.exists():
        return []
    available = {fp.stem for fp in patterns_dir.iterdir() if fp.is_file() and fp.suffix == ".md"}
    missing: list[tuple[str, str]] = []
    for cid, cluster in bridge.get("symptom_clusters", {}).items():
        for pid in cluster.get("associated_patterns", []) or []:
            pid_str = str(pid)
            if pid_str in available or pid_str.removeprefix("pattern_") in available:
                continue
            missing.append((cid, pid_str))
    return missing


def find_duplicate_names(bridge: dict[str, Any]) -> dict[str, list[str]]:
    """``{standard_name: [cluster_ids]}`` for names shared by multiple clusters."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for cid, cluster in bridge.get("symptom_clusters", {}).items():
        name = (cluster.get("standard_name") or "").strip()
        if not name:
            continue
        by_name[name].append(cid)
    return {n: ids for n, ids in by_name.items() if len(ids) > 1}


def find_stale_demotions(
    bridge: dict[str, Any], stale_days: int = 30, *, now: datetime | None = None
) -> list[tuple[str, str | None]]:
    """``priority == -1`` clusters whose last positive signal is older than ``stale_days``.

    We look at the cluster's optional ``last_positive_ts`` / ``last_seen``
    field if present, else fall back to "no data" (still flagged so the
    operator can decide to archive).
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=stale_days)
    stale: list[tuple[str, str | None]] = []
    for cid, cluster in bridge.get("symptom_clusters", {}).items():
        if cluster.get("priority") != -1:
            continue
        ts_raw = cluster.get("last_positive_ts") or cluster.get("last_seen")
        if not ts_raw:
            stale.append((cid, None))
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            stale.append((cid, str(ts_raw)))
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            stale.append((cid, ts.isoformat()))
    return stale


def lint(bridge_path: Path, patterns_dir: Path, *, stale_days: int) -> dict[str, Any]:
    bridge = _load_bridge(bridge_path)
    report = {
        "bridge_path": str(bridge_path),
        "cluster_count": len(bridge.get("symptom_clusters", {})),
        "orphan_patterns": find_orphan_patterns(bridge, patterns_dir),
        "missing_pattern_files": find_missing_pattern_files(bridge, patterns_dir),
        "duplicate_names": find_duplicate_names(bridge),
        "stale_demotions": find_stale_demotions(bridge, stale_days=stale_days),
    }
    report["anomaly_count"] = (
        len(report["orphan_patterns"])
        + len(report["missing_pattern_files"])
        + len(report["duplicate_names"])
        + len(report["stale_demotions"])
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bridge", type=Path, default=ASSETS_DIR / "bridge_index.json")
    ap.add_argument("--patterns-dir", type=Path, default=CODE_PATTERNS_DIR)
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--json", type=Path, default=None, help="Write JSON report here.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    report = lint(args.bridge, args.patterns_dir, stale_days=args.stale_days)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"report → {args.json}")

    print(f"clusters:               {report['cluster_count']}")
    print(f"orphan pattern files:   {len(report['orphan_patterns'])}")
    for name in report["orphan_patterns"][:5]:
        print(f"    - {name}")
    if len(report["orphan_patterns"]) > 5:
        print(f"    … (+{len(report['orphan_patterns'])-5} more)")
    print(f"missing pattern files:  {len(report['missing_pattern_files'])}")
    for cid, pid in report["missing_pattern_files"][:5]:
        print(f"    - cluster {cid} references missing {pid}")
    print(f"duplicate names:        {len(report['duplicate_names'])}")
    for name, ids in list(report["duplicate_names"].items())[:5]:
        print(f"    - '{name[:60]}' shared by {len(ids)} clusters")
    print(f"stale demotions:        {len(report['stale_demotions'])}")
    for cid, ts in report["stale_demotions"][:5]:
        print(f"    - {cid} last_seen={ts!r}")

    print()
    print(f"anomalies total: {report['anomaly_count']}")
    return 1 if report["anomaly_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
