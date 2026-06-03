#!/usr/bin/env python3
"""Sprint 6: distil evidence traces into per-pattern stats (plan §11.8).

Walks every ``data/exports/evidence_traces*.jsonl`` (so multiple
date-stamped exports can coexist), streams them through
:func:`evidence_distill.distill`, and writes the aggregated stats to
``data/assets/evidence_stats.json``.

Sprint-6 acceptance gates (plan):

* every CATALYST trace carries an ``injection_id``;
* ≥ 80% of CATALYST traces carry ``post_score_3`` and ``post_score_5``;
* ≥ 50% of CATALYST traces carry a non-empty ``code_diff_summary``.

The CLI exits non-zero on any gate violation unless
``--allow-violations`` is passed.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

from rosclaw_know import config
from rosclaw_know.evidence_distill import (
    distill,
    is_demoted,
    is_promoted,
    write_stats,
)
from rosclaw_know.evidence_writer import stream_traces
from rosclaw_know.schemas import EvidenceTrace

logger = logging.getLogger("distill_evidence")


_DEFAULT_EXPORT_GLOB = "evidence_traces*.jsonl"


def _discover_exports(root: Path) -> list[Path]:
    """List all JSONL files under ``root`` matching the trace glob.

    Sorted lex so chronologically-named files (``..._20260603.jsonl``)
    aggregate in a deterministic order.
    """
    if not root.exists():
        return []
    files = sorted(root.glob(_DEFAULT_EXPORT_GLOB))
    return [p for p in files if p.is_file()]


def _stream_all(paths: Iterable[Path]) -> Iterable[EvidenceTrace]:
    for p in paths:
        yield from stream_traces(p)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Distil evidence traces into per-pattern stats.",
    )
    default_in = config.PROJECT_ROOT / "data" / "exports"
    p.add_argument(
        "--exports-dir", default=str(default_in),
        help=(
            "Directory containing evidence_traces*.jsonl "
            "(default: data/exports/)."
        ),
    )
    p.add_argument(
        "--out", default=str(config.ASSETS_DIR / "evidence_stats.json"),
    )
    p.add_argument(
        "--allow-violations", action="store_true",
        help="Exit 0 even when plan §Sprint 6 acceptance is violated.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    exports_dir = Path(args.exports_dir)
    paths = _discover_exports(exports_dir)
    if not paths:
        print(
            f"No evidence_traces*.jsonl found under {exports_dir}.",
            file=sys.stderr,
        )
        return 2
    print(f"Reading {len(paths)} JSONL file(s):")
    for p in paths:
        print(f"  - {p}")

    stats, coverage = distill(_stream_all(paths))
    out_path = write_stats(stats, coverage, out_path=Path(args.out))

    print()
    print(f"Wrote {len(stats)} pattern stats to {out_path}")
    print()
    print(f"Coverage:  total={coverage.total}  catalyst={coverage.catalyst_total}")
    if coverage.catalyst_total > 0:
        print(
            f"  injection_id:        "
            f"{coverage.catalyst_with_injection_id}/{coverage.catalyst_total} "
            f"({coverage.catalyst_with_injection_id / coverage.catalyst_total:.0%})"
        )
        print(
            f"  post_score_3:        "
            f"{coverage.catalyst_with_post_score_3}/{coverage.catalyst_total} "
            f"({coverage.catalyst_with_post_score_3 / coverage.catalyst_total:.0%})"
        )
        print(
            f"  post_score_5:        "
            f"{coverage.catalyst_with_post_score_5}/{coverage.catalyst_total} "
            f"({coverage.catalyst_with_post_score_5 / coverage.catalyst_total:.0%})"
        )
        print(
            f"  code_diff_summary:   "
            f"{coverage.catalyst_with_code_diff_summary}/{coverage.catalyst_total} "
            f"({coverage.catalyst_with_code_diff_summary / coverage.catalyst_total:.0%})"
        )

    if stats:
        print()
        print("Per-pattern verdicts:")
        for pid, stat in sorted(stats.items()):
            verdict = (
                "PROMOTE" if is_promoted(stat)
                else "DEMOTE" if is_demoted(stat)
                else "HOLD"
            )
            adj = stat.placebo_adjusted_uplift
            adj_str = "n/a" if adj is None else f"{adj:+.4f}"
            print(
                f"  [{verdict:7s}] {pid:60s}  "
                f"placebo_adj={adj_str}  "
                f"n_true={stat.n_by_arm.get('true', 0)}  "
                f"hint_use={stat.hint_use_rate:.0%}"
            )

    if coverage.violations:
        print()
        print(f"VIOLATIONS ({len(coverage.violations)}):", file=sys.stderr)
        for v in coverage.violations:
            print(f"  - {v}", file=sys.stderr)
        if not args.allow_violations:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
