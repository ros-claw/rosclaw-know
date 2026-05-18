#!/usr/bin/env python3
"""scripts/analyze_stats.py — Phase 6 pattern trajectory analyser.

Polls rosclaw-how's ``/wiki/v1/stats`` once, persists the snapshot, runs
linear regression over the last ``--window`` snapshots, and writes:

  data/stats_history/stats-<ISO>.json    raw snapshot
  data/reports/trends.json               machine-readable
  data/reports/pattern_trends.md         human-readable

Run from cron / systemd-timer / k8s CronJob hourly or daily.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.stats_analyze import (  # noqa: E402
    DEFAULT_STATS_URL,
    REPORTS_DIR,
    STATS_HISTORY_DIR,
    run,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_STATS_URL)
    ap.add_argument(
        "--no-snapshot", action="store_true",
        help="Skip the live fetch; analyse existing snapshots only.",
    )
    ap.add_argument("--window", type=int, default=10,
                    help="Last N snapshots used for trend slope (default 10).")
    ap.add_argument("--history-dir", type=Path, default=STATS_HISTORY_DIR)
    ap.add_argument("--out-dir", type=Path, default=REPORTS_DIR)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    summary = run(
        url=args.url,
        snapshot_now=not args.no_snapshot,
        history_dir=args.history_dir,
        out_dir=args.out_dir,
        window=args.window,
    )
    print(
        f"snapshots={summary['snapshots']} "
        f"patterns_tracked={summary['patterns_tracked']} "
        f"trends={summary['trends_json']} "
        f"report={summary['report_md']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
