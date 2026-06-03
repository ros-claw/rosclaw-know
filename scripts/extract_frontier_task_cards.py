#!/usr/bin/env python3
"""Sweep a Frontier-Eng (or Arena) ``benchmarks/`` tree and emit a
fully-typed ``task_cards.yaml`` catalog.

Sprint 2 deliverable (v1.5 plan §11.3, §5.2).

Usage::

    # Default: read FRONTIER_ENG_BENCHMARKS env var, write
    # data/assets/task_cards.yaml.
    python scripts/extract_frontier_task_cards.py --apply

    # Explicit paths:
    python scripts/extract_frontier_task_cards.py \\
        --benchmarks-root /path/to/Frontier-Engineering/benchmarks \\
        --out data/assets/task_cards.yaml --apply

    # Dry-run (default) — print summary, no write.
    python scripts/extract_frontier_task_cards.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import yaml

from rosclaw_know import config
from rosclaw_know.extractors import extract_from_corpus
from rosclaw_know.schemas import SCHEMA_VERSION, TaskCard

logger = logging.getLogger("extract_frontier_task_cards")


# ── output shape ──────────────────────────────────────────────────────


def _dump_cards(cards: list[TaskCard]) -> dict:
    """Wrap the card list in a stable doc envelope.

    Format::

        schema_version: "2.0"
        benchmark: "frontier-eng"
        n_tasks: 74
        task_cards:
          - id: task_robotics_pid_tuning
            ...
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "frontier-eng",
        "n_tasks": len(cards),
        "task_cards": [c.model_dump(exclude_defaults=False) for c in cards],
    }


def _summary(cards: list[TaskCard]) -> dict[str, object]:
    """Counters useful for the eye + CI gate."""
    return {
        "n_total":                len(cards),
        "n_with_obj_direction":   sum(1 for c in cards if c.objective_direction),
        "n_with_artifact_type":   sum(1 for c in cards if c.artifact_type),
        "n_with_verifier_type":   sum(1 for c in cards if c.verifier_type),
        "n_with_hard_constraints": sum(1 for c in cards if c.hard_constraints),
        "n_with_failure_modes":   sum(1 for c in cards if c.common_failure_modes),
        "by_domain":              dict(Counter(c.domain for c in cards)),
        "by_artifact_type":       dict(Counter(c.artifact_type for c in cards)),
        "by_verifier_type":       dict(Counter(c.verifier_type for c in cards)),
        "by_direction":           dict(Counter(c.objective_direction for c in cards)),
    }


# ── CLI ───────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract TaskCard catalog from a Frontier-Eng "
                    "benchmarks/ tree.",
    )
    p.add_argument(
        "--benchmarks-root",
        default=os.environ.get(
            "FRONTIER_ENG_BENCHMARKS",
            "/root/workspace/rosclaw/rosclaw_wiki/Frontier-Engineering/benchmarks",
        ),
        help="Frontier-Eng benchmarks/ directory (or env "
             "FRONTIER_ENG_BENCHMARKS).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Destination YAML path "
             "(default: data/assets/task_cards.yaml).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the catalog.  Without --apply the script is dry-run.",
    )
    p.add_argument(
        "--min-cards",
        type=int,
        default=47,
        help="Acceptance gate: refuse to write if fewer than this "
             "many cards were produced (default 47, the Frontier-Eng "
             "v1 task count).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG-level logging.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    benchmarks_root = Path(args.benchmarks_root)
    if not benchmarks_root.is_dir():
        print(
            f"ERROR: benchmarks root {benchmarks_root} not found",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.out) if args.out else (config.ASSETS_DIR / "task_cards.yaml")

    cards = extract_from_corpus(benchmarks_root)
    summary = _summary(cards)

    # Print summary so the operator can eyeball coverage.
    print(f"Extracted {summary['n_total']} task cards from {benchmarks_root}")
    for k, v in summary.items():
        if k == "n_total":
            continue
        print(f"  {k:>26}: {v}")

    if summary["n_total"] < args.min_cards:
        print(
            f"\nFAIL: only {summary['n_total']} cards produced (gate ≥ "
            f"{args.min_cards}). Refusing to write.",
            file=sys.stderr,
        )
        return 1

    # Acceptance §11.3 — every card MUST have these three.
    if (
        summary["n_with_obj_direction"] != summary["n_total"]
        or summary["n_with_artifact_type"] != summary["n_total"]
        or summary["n_with_verifier_type"] != summary["n_total"]
    ):
        print(
            "FAIL: not every card has objective_direction / artifact_type / "
            "verifier_type.  Schema bug?",
            file=sys.stderr,
        )
        return 1

    if not args.apply:
        print(f"\nDRY-RUN — would write {out_path}")
        print("(use --apply to write)")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            _dump_cards(cards),
            fh,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    tmp.replace(out_path)
    print(f"\nOK  wrote {out_path} ({summary['n_total']} cards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
