#!/usr/bin/env python3
"""Sprint 11: ingest real-robot trace JSONL → evidence_stats.json.

Closes the Sprint 9 → Sprint 6 self-improvement loop:

  rosbag / Isaac / MuJoCo / Foxglove RobotEvent JSONL
              │
              │  read_robot_event_jsonl
              v
       list[RobotEvent]    (carrying ``task_run`` envelopes)
              │
              │  events_to_evidence_traces
              v
      list[EvidenceTrace]
              │
              │  evidence_distill.distill
              v
   dict[pattern_id, EvidenceStat]
              │
              │  evidence_distill.write_stats
              v
   data/assets/evidence_stats.json   (consumed by bridge_reweighter)

Usage::

    python scripts/ingest_robot_evidence.py \\
        --robot-events tests/fixtures/sprint11/robot_traces_with_evidence.jsonl \\
        --out /tmp/evidence_stats.json

The output is the same JSON shape ``feedback_distill`` produces from
Frontier-Eng OpenEvolve logs — so the bridge reweighter consumes both
sources transparently.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from rosclaw_know.evidence_distill import (
    distill,
    is_demoted,
    is_promoted,
    write_stats,
)
from rosclaw_know.sim_ingest import (
    events_to_evidence_traces,
    read_robot_event_jsonl,
)

log = logging.getLogger("ingest_robot_evidence")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--robot-events", action="append", default=[], required=True,
        help="Path to a RobotEvent JSONL file (Sprint 9 ingest output OR raw). "
             "Repeatable.",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Where to write evidence_stats.json (default: "
             "data/assets/evidence_stats.json).",
    )
    p.add_argument(
        "--print-trace-jsonl", type=Path, default=None,
        help="Optional: also dump the intermediate EvidenceTrace stream "
             "to this path (one JSON per line).",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show per-pattern verdict on stderr.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 1. Read all RobotEvent JSONL inputs.
    all_events: list = []
    for raw_path in args.robot_events:
        path = Path(raw_path)
        evs = read_robot_event_jsonl(path)
        log.info("read %d events from %s", len(evs), path)
        all_events.extend(evs)

    if not all_events:
        log.warning("no events ingested — nothing to write")
        return 1

    # 2. Convert to EvidenceTrace where the task_run envelope is present.
    traces = events_to_evidence_traces(all_events)
    log.info(
        "converted %d/%d events into evidence traces (skipped %d without task_run)",
        len(traces), len(all_events), len(all_events) - len(traces),
    )

    if not traces:
        log.warning(
            "no events carried a task_run envelope — nothing to distill. "
            "Add fields {task_name, run_id, pre_score, ...} to your events.",
        )
        return 1

    # 3. Optional: dump intermediate traces.
    if args.print_trace_jsonl is not None:
        out_traces = Path(args.print_trace_jsonl)
        out_traces.parent.mkdir(parents=True, exist_ok=True)
        with out_traces.open("w", encoding="utf-8") as fh:
            for t in traces:
                fh.write(t.model_dump_json() + "\n")
        log.info("wrote %d traces to %s", len(traces), out_traces)

    # 4. Distill → per-pattern EvidenceStat.
    stats, coverage = distill(traces)

    if coverage.violations:
        log.warning("coverage violations:")
        for v in coverage.violations:
            log.warning("  - %s", v)
    else:
        log.info("coverage gates clean")

    if args.verbose:
        for pid, stat in sorted(stats.items()):
            verdict = (
                "PROMOTE" if is_promoted(stat)
                else "DEMOTE" if is_demoted(stat)
                else "hold"
            )
            log.info(
                "%s  pid=%s  n_true=%d  n_placebo=%d  adj=%.3f  verdict=%s",
                verdict.rjust(7),
                pid,
                stat.n_by_arm["true"],
                stat.n_by_arm["placebo"],
                stat.placebo_adjusted_uplift or 0.0,
                verdict,
            )

    # 5. Write evidence_stats.json (consumed by bridge_reweighter).
    out_path = write_stats(stats, coverage, out_path=args.out)
    log.info("wrote evidence_stats to %s (%d patterns)", out_path, len(stats))

    # Print summary on stdout for piping in shell.
    promoted = sum(1 for s in stats.values() if is_promoted(s))
    demoted = sum(1 for s in stats.values() if is_demoted(s))
    print(
        json.dumps({
            "events_read": len(all_events),
            "traces_emitted": len(traces),
            "patterns_with_stats": len(stats),
            "promoted": promoted,
            "demoted": demoted,
            "evidence_stats_path": str(out_path),
        }, indent=2),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
