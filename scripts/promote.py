#!/usr/bin/env python3
"""scripts/promote.py — Phase 7 staging maturation gate.

Reads ``/wiki/v1/stats`` and applies the lifecycle rule:

  * ``priority == 0`` (staging) + ``n ≥ MIN`` + ``uplift_mean > +0.05``
        → POST /admin/promote {delta: +1}   (graduate to production)
  * ``priority`` unset/production + ``n ≥ MIN`` + ``uplift_mean < -0.05``
        → POST /admin/promote {delta: -1}   (soft-deprecate)
  * ``priority == 0`` + ``n ≥ MIN`` + ``uplift_mean < -0.05``
        → POST /admin/promote {delta: -1}   (skip the production tier)

The script runs in dry-run mode by default. Pass ``--apply`` to actually
issue the HTTP calls.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import ASSETS_DIR  # noqa: E402

logger = logging.getLogger("rosclaw_know.promote")

# Promotion / demotion thresholds. Match bridge_reweighter's logic.
MIN_SAMPLE_SIZE = 5
PROMOTE_UPLIFT = 0.05
DEMOTE_UPLIFT = -0.05


@dataclass(frozen=True)
class Candidate:
    pattern_id: str
    current_priority: int | None
    n: int
    uplift_mean: float
    win_rate: float
    delta: int  # +1 to promote, -1 to demote
    reason: str


def _bridge_priorities() -> dict[str, int]:
    """Read current priorities from bridge_index.json (None if unset)."""
    bridge_path = ASSETS_DIR / "bridge_index.json"
    if not bridge_path.exists():
        return {}
    try:
        data = json.loads(bridge_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, int] = {}
    for cid, cluster in data.get("symptom_clusters", {}).items():
        if "priority" in cluster:
            try:
                out[str(cid)] = int(cluster["priority"])
            except (TypeError, ValueError):
                continue
    # Also map by pattern_id (associated_patterns) — /stats keys by pattern,
    # not cluster id.
    for cid, cluster in data.get("symptom_clusters", {}).items():
        prio = cluster.get("priority")
        if prio is None:
            continue
        for pid in cluster.get("associated_patterns") or []:
            out.setdefault(str(pid), int(prio))
    return out


def _fetch_stats(base: str) -> dict[str, dict[str, Any]]:
    url = f"{base.rstrip('/')}/wiki/v1/stats"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.error("Could not fetch /stats: %s", exc)
        return {}
    # Phase 7 #54: /stats returns bucketed shape
    # {staging: {...}, production: {...}, demoted: {...}, unbucketed: {...}}.
    # Flatten into a single {pattern_id: stats} dict for the classifier.
    if not isinstance(payload, dict):
        return {}
    flat: dict[str, dict[str, Any]] = {}
    for bucket in ("staging", "production", "demoted", "unbucketed"):
        for pid, agg in (payload.get(bucket) or {}).items():
            if isinstance(agg, dict):
                flat[str(pid)] = agg
    return flat


def _classify(pid: str, agg: dict[str, Any], priorities: dict[str, int]) -> Candidate | None:
    n = int(agg.get("n", 0))
    if n < MIN_SAMPLE_SIZE:
        return None
    uplift = float(agg.get("avg_uplift", 0.0))
    win_rate = float(agg.get("win_rate", 0.0))
    cur = priorities.get(pid)
    # Staging → production promotion
    if cur == 0 and uplift > PROMOTE_UPLIFT:
        return Candidate(pid, cur, n, uplift, win_rate, +1, "staging→production: uplift > +0.05")
    # Staging → demoted skip-production
    if cur == 0 and uplift < DEMOTE_UPLIFT:
        return Candidate(pid, cur, n, uplift, win_rate, -1, "staging→demoted: uplift < -0.05")
    # Production → demoted
    if cur in (None, 1) and uplift < DEMOTE_UPLIFT:
        return Candidate(pid, cur, n, uplift, win_rate, -1, "production→demoted: uplift < -0.05")
    return None


def find_candidates(base: str) -> list[Candidate]:
    stats = _fetch_stats(base)
    if not stats:
        return []
    priorities = _bridge_priorities()
    cands: list[Candidate] = []
    for pid, agg in stats.items():
        if not isinstance(agg, dict):
            continue
        c = _classify(str(pid), agg, priorities)
        if c is not None:
            cands.append(c)
    return cands


def _apply(base: str, api_key: str, candidate: Candidate, timeout: int = 30) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/wiki/v1/admin/promote"
    body = {"pattern_id": candidate.pattern_id, "delta": candidate.delta}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        return {"_http_error": exc.code, "_body": body_text}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--api-key", default="rw_sk_dev_local")
    ap.add_argument(
        "--apply", action="store_true",
        help="Actually POST to /admin/promote. Default is dry-run.",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    cands = find_candidates(args.base)
    if not cands:
        print("no promotion candidates (no patterns crossed thresholds)")
        return 0

    print(f"Found {len(cands)} candidate(s):\n")
    for c in cands:
        verb = "PROMOTE" if c.delta > 0 else "DEMOTE"
        print(
            f"  {verb}  {c.pattern_id}  (priority={c.current_priority} "
            f"→ {c.current_priority + c.delta if c.current_priority is not None else c.delta})  "
            f"n={c.n} uplift={c.uplift_mean:+.3f} win_rate={c.win_rate:.2f}  — {c.reason}"
        )

    if not args.apply:
        print("\n(dry-run; pass --apply to commit)")
        return 0

    print("\nApplying...")
    for c in cands:
        resp = _apply(args.base, args.api_key, c)
        print(f"  {c.pattern_id} → {resp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
