#!/usr/bin/env python3
"""Find synth clusters that compete with curated patterns.

docs/know-how下一步建议.md §7 — observed iter4_p1 T_001 PIDTuning issue:
the synth cluster ``pid_antiwindup`` (long standard_name including the
phrase "anti-windup / back calculation / saturation") outranks the
curated ``anti_windup_pid`` on cosine retrieval. HOW's curated rescue
doesn't fire because the curated isn't in top-K (the synth dominates).

Similar pattern observed earlier for motion_blur (the synth
motion_blur_decomposition_with_cross-shutter_guidance outranking the
curated motion_blur_imu_aided_deblur — fixed via canary sibling, but
the underlying issue persists in the routing).

This script scans the live bridge for cases where:
  - A synth cluster (source != "curated") is in the same `domain` as a
    curated cluster
  - Their standard_names share ≥ N tokens (default 4)
  - OR matched_keywords overlap above K (default 5)

These are candidates for one of:
  (a) demoting the synth (set metadata.lifecycle_status="demoted" so
      infer_source_tier returns F_DEMOTED → HOW skips)
  (b) sibling registration in routing_canary.json
  (c) merging — if the synth is genuinely the same concept, the curated
      version should absorb its keywords and the synth should be deleted

Output: data/reports/curated_synth_competition.json

Usage::

    python scripts/find_curated_synth_competition.py [--threshold N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know import config  # noqa: E402


def _tokens(text: str) -> set[str]:
    return {t for t in (
        "".join(c if c.isalnum() else " " for c in (text or "").lower()).split()
    ) if len(t) >= 3}


def find_competition(
    bridge: dict,
    *,
    standard_name_overlap: int = 4,
    keyword_overlap: int = 5,
) -> list[dict]:
    """Return list of (curated, synth) competition records."""
    clusters = bridge.get("symptom_clusters", {})
    curated = {cid: c for cid, c in clusters.items() if isinstance(c, dict) and c.get("source") == "curated"}
    synth = {cid: c for cid, c in clusters.items() if isinstance(c, dict) and c.get("source") != "curated"}

    records: list[dict] = []
    for cur_id, cur in curated.items():
        cur_tokens = _tokens(cur.get("standard_name", ""))
        cur_kws = {k.lower() for k in (cur.get("matched_keywords") or [])}
        cur_domain = cur.get("domain")
        for s_id, s in synth.items():
            if s.get("domain") != cur_domain:
                continue
            s_tokens = _tokens(s.get("standard_name", ""))
            s_kws = {k.lower() for k in (s.get("matched_keywords") or [])}
            shared_tokens = cur_tokens & s_tokens
            shared_kws = cur_kws & s_kws
            if len(shared_tokens) >= standard_name_overlap or len(shared_kws) >= keyword_overlap:
                records.append({
                    "curated": cur_id,
                    "curated_tier": cur.get("source_tier"),
                    "synth": s_id,
                    "synth_tier": s.get("source_tier"),
                    "domain": cur_domain,
                    "standard_name_shared_tokens": sorted(shared_tokens),
                    "n_shared_standard_name_tokens": len(shared_tokens),
                    "n_shared_keywords": len(shared_kws),
                    "shared_keywords": sorted(shared_kws),
                    "synth_standard_name_chars": len(s.get("standard_name") or ""),
                    "curated_standard_name_chars": len(cur.get("standard_name") or ""),
                })
    # Sort by severity: most token overlap first.
    records.sort(key=lambda r: -r["n_shared_standard_name_tokens"])
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--standard-name-overlap",
        type=int,
        default=4,
        help="Min shared tokens in standard_name to flag (default 4)",
    )
    ap.add_argument(
        "--keyword-overlap",
        type=int,
        default=5,
        help="Min shared matched_keywords to flag (default 5)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "data" / "reports" / "curated_synth_competition.json",
    )
    args = ap.parse_args()

    bridge_path = config.ASSETS_DIR / "bridge_index.json"
    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    records = find_competition(
        bridge,
        standard_name_overlap=args.standard_name_overlap,
        keyword_overlap=args.keyword_overlap,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thresholds": {
            "standard_name_overlap": args.standard_name_overlap,
            "keyword_overlap": args.keyword_overlap,
        },
        "competition_count": len(records),
        "records": records,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not records:
        print(f"[find-competition] no curated-synth competition detected (thresholds: "
              f"std-name≥{args.standard_name_overlap}, kw≥{args.keyword_overlap})")
        return 0

    print(f"[find-competition] {len(records)} curated-synth competition record(s) found")
    print("=" * 80)
    for r in records:
        print(f"curated  {r['curated']:42s}  tier={r['curated_tier']}")
        print(f"  vs synth  {r['synth']:42s}  tier={r['synth_tier']}")
        print(f"  domain={r['domain']}, "
              f"shared standard_name tokens={r['n_shared_standard_name_tokens']} "
              f"({len(r['standard_name_shared_tokens'])} unique), "
              f"shared keywords={r['n_shared_keywords']}")
        print(f"  curated standard_name: {r['curated_standard_name_chars']} chars, "
              f"synth: {r['synth_standard_name_chars']} chars")
        print()
    print(f"[find-competition] report → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
