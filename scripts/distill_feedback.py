#!/usr/bin/env python3
"""scripts/distill_feedback.py — CLI for Phase 4 feedback distillation.

Reads ``outcomes-*.jsonl`` exports from rosclaw-how, computes per-pattern
metrics, and writes ``data/assets/pattern_metrics.json`` for downstream
bridge_index reweighting.

Usage:

    .venv/bin/python scripts/distill_feedback.py
    .venv/bin/python scripts/distill_feedback.py --exports-dir /custom/path
    .venv/bin/python scripts/distill_feedback.py --summary    # print top/bottom 5
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.feedback_distill import (  # noqa: E402
    DEFAULT_EXPORTS_DIR,
    PatternMetric,
    distill,
    is_demoted,
)


def _summary_line(m: PatternMetric) -> str:
    return (
        f"  {m.pattern_id:32s} n={m.n:4d}  "
        f"uplift={m.uplift_mean:+.3f}±{m.uplift_std:.3f}  "
        f"win_rate={m.win_rate:.2f}  last={m.last_seen[:10]}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--exports-dir", type=Path, default=DEFAULT_EXPORTS_DIR,
        help=f"Directory with outcomes-*.jsonl files (default: {DEFAULT_EXPORTS_DIR})",
    )
    ap.add_argument(
        "--out", type=Path, default=None,
        help="Override output metrics file path (default: data/assets/pattern_metrics.json)",
    )
    ap.add_argument(
        "--summary", action="store_true",
        help="Print top/bottom 5 patterns by uplift_mean after distillation.",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    metrics = distill(exports_dir=args.exports_dir, out_path=args.out)

    if not args.summary or not metrics:
        return 0

    ranked = sorted(metrics.values(), key=lambda m: m.uplift_mean, reverse=True)
    print(f"\nTop {min(5, len(ranked))} by uplift_mean:")
    for m in ranked[:5]:
        print(_summary_line(m))
    if len(ranked) > 5:
        print(f"\nBottom {min(5, len(ranked))} by uplift_mean:")
        for m in ranked[-5:][::-1]:
            mark = " (DEMOTED)" if is_demoted(m) else ""
            print(_summary_line(m) + mark)
    return 0


if __name__ == "__main__":
    sys.exit(main())
