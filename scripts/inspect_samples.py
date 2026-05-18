#!/usr/bin/env python3
"""Sample N extracted heuristics for manual quality audit.

Prints each sample with: page_path | symptom | domain | fix_pattern.
Used to validate the 85% accuracy gate before running the full corpus.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.infra import open_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="how many samples to display")
    ap.add_argument("--domain", type=str, default=None, help="filter by domain")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of pretty text")
    args = ap.parse_args()

    with open_db() as conn:
        q = "SELECT id, page_path, symptom, domain, fix_pattern, failed_attempt FROM heuristics"
        params: tuple = ()
        if args.domain:
            q += " WHERE domain = ?"
            params = (args.domain,)
        rows = conn.execute(q, params).fetchall()

    if not rows:
        print("No heuristics found. Did you run the harvester yet?")
        return 1

    sample = random.sample(rows, min(args.n, len(rows)))

    if args.json:
        print(json.dumps([dict(r) for r in sample], indent=2, ensure_ascii=False))
        return 0

    for i, r in enumerate(sample, 1):
        print("─" * 80)
        print(f"[{i}/{len(sample)}]  {r['page_path']}")
        print(f"  domain : {r['domain']}")
        print(f"  symptom: {r['symptom']}")
        print(f"  fix    : {r['fix_pattern']}")
        if r["failed_attempt"]:
            print(f"  failed : {r['failed_attempt']}")
    print("─" * 80)
    print(f"Total in DB: {len(rows)}; sampled: {len(sample)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
