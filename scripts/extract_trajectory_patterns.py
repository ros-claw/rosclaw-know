#!/usr/bin/env python3
"""Mine candidate patterns from ``baseline_archive/`` (Sprint 3, plan §11.4).

The Frontier-Eng repo ships a ``baseline_archive/`` tree with the
final-best program for each ``(experiment, algorithm, model, task)``
quadruple.  We treat every such quadruple as a *one-step trajectory*
(baseline → final-best) and run the trajectory-extractor pipeline
over it.

Each one-step trajectory produces zero-or-more CandidatePattern
objects via the registered feature extractors.  Across the corpus we
then *merge* candidates with the same id, summing
``evidence_count`` and combining ``source_trajectory_ids`` — the
plan §3.5 says a candidate needs ``evidence_count >= 2`` before it
can be promoted to a real pattern (Sprint 4).

Usage::

    # Dry-run (default)
    python scripts/extract_trajectory_patterns.py

    # Write data/assets/trajectory_patterns.yaml
    python scripts/extract_trajectory_patterns.py --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from rosclaw_know import config
from rosclaw_know.extractors import (
    extract_candidate_patterns,
    from_baseline_archive_pair,
)
from rosclaw_know.schemas import CandidatePattern, Mutation, SCHEMA_VERSION

logger = logging.getLogger("extract_trajectory_patterns")


_TASK_DIR_RE = re.compile(r"^(?P<family>[A-Za-z]+)_(?P<task>[\w-]+)$")
"""``Robotics_PIDTuning`` → family=Robotics, task=PIDTuning.
Tasks like ``EngDesign`` (no underscore) are skipped because they
collapse over multiple sub-tasks in baseline_archive."""


_BASELINE_CANDIDATES = (
    "scripts/init.py",
    "baseline/init.py",
    "baseline/submission.py",
    "scripts/baseline.py",
)


def _find_baseline_for_task(benchmarks_root: Path, family: str, task: str) -> Path | None:
    """Locate the editable baseline file for a Frontier-Eng task."""
    task_dir = benchmarks_root / family / task
    if not task_dir.is_dir():
        # Some tasks have non-Camel family names — try a relaxed lookup
        for cand in benchmarks_root.iterdir():
            if cand.is_dir() and cand.name.lower() == family.lower():
                task_dir = cand / task
                break
    for sub in _BASELINE_CANDIDATES:
        p = task_dir / sub
        if p.is_file():
            return p
    return None


def _iter_archive_programs(archive_root: Path):
    """Yield ``(experiment, algorithm, model, family, task_name, program_path)``
    tuples for every ``program.{py,cpp,c}`` in the archive."""
    for prog in archive_root.rglob("program.*"):
        if prog.suffix not in (".py", ".cpp", ".c"):
            continue
        # archive_root / experiment / algorithm / model / <family_task> / program.*
        try:
            rel = prog.relative_to(archive_root)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) < 5:
            continue
        experiment, algorithm, model, task_dir = parts[0], parts[1], parts[2], parts[3]
        m = _TASK_DIR_RE.match(task_dir)
        if not m:
            continue
        yield experiment, algorithm, model, m.group("family"), m.group("task"), prog


def _merge_candidates(candidates: list[CandidatePattern]) -> list[CandidatePattern]:
    """Merge candidates with the same id across trajectories.

    Sum ``evidence_count``, take the union of ``successful_mutations``
    (deduped on (kind, target_identifier)), take the mean of
    ``avg_score_delta``, and concatenate ``source_trajectory_ids``.
    """
    by_id: dict[str, list[CandidatePattern]] = defaultdict(list)
    for c in candidates:
        by_id[c.id].append(c)

    merged: list[CandidatePattern] = []
    for cid, group in sorted(by_id.items()):
        if len(group) == 1:
            merged.append(group[0])
            continue
        first = group[0]
        # Successful mutations union (dedup on kind+target_identifier)
        seen: set[tuple[str, str | None]] = set()
        all_muts: list[Mutation] = []
        for cp in group:
            for m in cp.successful_mutations:
                key = (m.kind, m.target_identifier)
                if key not in seen:
                    seen.add(key)
                    all_muts.append(m)
        deltas = [cp.avg_score_delta for cp in group if cp.avg_score_delta is not None]
        avg_delta = statistics.fmean(deltas) if deltas else None
        ids: list[str] = []
        for cp in group:
            ids.extend(cp.source_trajectory_ids)
        merged.append(first.model_copy(update={
            "successful_mutations": all_muts,
            "evidence_count": len(group),
            "avg_score_delta": avg_delta,
            "source_trajectory_ids": ids,
        }))
    return merged


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mine candidate patterns from Frontier-Eng baseline_archive.",
    )
    p.add_argument(
        "--archive-root",
        default=os.environ.get(
            "FRONTIER_ENG_BASELINE_ARCHIVE",
            "/root/workspace/rosclaw/rosclaw_wiki/Frontier-Engineering/baseline_archive",
        ),
        help="baseline_archive root (or env FRONTIER_ENG_BASELINE_ARCHIVE).",
    )
    p.add_argument(
        "--benchmarks-root",
        default=os.environ.get(
            "FRONTIER_ENG_BENCHMARKS",
            "/root/workspace/rosclaw/rosclaw_wiki/Frontier-Engineering/benchmarks",
        ),
        help="Frontier-Eng benchmarks/ root (or env FRONTIER_ENG_BENCHMARKS).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Destination YAML "
             "(default: data/assets/trajectory_patterns.yaml).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the catalog.  Without --apply the script is dry-run.",
    )
    p.add_argument(
        "--min-candidates",
        type=int,
        default=4,
        help="Acceptance gate: refuse to write if fewer than this many "
             "merged candidate patterns were produced (default 4; "
             "Sprint 3 ships with the PID extractor — AES/CUDA/sched "
             "extractors land later).",
    )
    p.add_argument(
        "--min-trajectories",
        type=int,
        default=10,
        help="Acceptance gate: refuse to write if fewer than this many "
             "trajectories were assembled (default 10).",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="DEBUG-level logging.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    archive_root = Path(args.archive_root)
    benchmarks_root = Path(args.benchmarks_root)
    if not archive_root.is_dir():
        print(f"ERROR: archive root {archive_root} not found", file=sys.stderr)
        return 2
    if not benchmarks_root.is_dir():
        print(f"ERROR: benchmarks root {benchmarks_root} not found", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else (
        config.ASSETS_DIR / "trajectory_patterns.yaml"
    )

    # Walk the archive, build a trajectory per (experiment, algorithm,
    # model, task), and run the extractors.
    n_skipped = 0
    n_trajectories = 0
    all_candidates: list[CandidatePattern] = []
    by_task_family: Counter[str] = Counter()

    for experiment, algorithm, model, family, task, prog_path in _iter_archive_programs(archive_root):
        baseline = _find_baseline_for_task(benchmarks_root, family, task)
        if baseline is None:
            n_skipped += 1
            continue
        try:
            baseline_text = baseline.read_text(encoding="utf-8")
            candidate_text = prog_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("could not read %s or %s: %s", baseline, prog_path, exc)
            n_skipped += 1
            continue

        traj_id = f"{experiment}__{algorithm}__{model}__{family}_{task}"
        traj = from_baseline_archive_pair(
            baseline_text=baseline_text,
            candidate_text=candidate_text,
            task_name=task,
            trajectory_id=traj_id,
            algorithm=algorithm,
            model=model,
        )
        n_trajectories += 1
        by_task_family[family] += 1
        all_candidates.extend(extract_candidate_patterns(traj))

    merged = _merge_candidates(all_candidates)

    # Summary
    print(f"Mined {n_trajectories} trajectories (skipped {n_skipped})")
    print(f"  trajectories by task family: {dict(by_task_family)}")
    print(f"Raw candidate patterns: {len(all_candidates)}")
    print(f"Merged candidate patterns: {len(merged)}")
    print()
    for c in sorted(merged, key=lambda c: -c.evidence_count):
        print(f"  - {c.id:55s}  evidence={c.evidence_count:3d}  "
              f"avg_delta={'-' if c.avg_score_delta is None else f'{c.avg_score_delta:+.3f}'}")
    print()

    # Acceptance gates
    if n_trajectories < args.min_trajectories:
        print(
            f"FAIL: only {n_trajectories} trajectories assembled "
            f"(gate ≥ {args.min_trajectories}).",
            file=sys.stderr,
        )
        return 1
    if len(merged) < args.min_candidates:
        print(
            f"FAIL: only {len(merged)} merged candidates "
            f"(gate ≥ {args.min_candidates}).",
            file=sys.stderr,
        )
        return 1

    # Leak guard — refuse to write any pattern whose description carries
    # a float literal.
    leak_re = re.compile(r"(?<![A-Za-z_])-?\d+\.\d+(?![A-Za-z_])")
    for c in merged:
        for m in c.successful_mutations + c.failed_mutations:
            if leak_re.search(m.description):
                print(
                    f"FAIL: leak detected in {c.id}: {m.description!r}",
                    file=sys.stderr,
                )
                return 1

    if not args.apply:
        print(f"DRY-RUN — would write {out_path}")
        print("(use --apply to write)")
        return 0

    doc = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "frontier-eng",
        "n_trajectories": n_trajectories,
        "n_candidates": len(merged),
        "candidate_patterns": [
            c.model_dump(exclude_defaults=False) for c in merged
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False, width=120)
    tmp.replace(out_path)
    print(f"OK  wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
