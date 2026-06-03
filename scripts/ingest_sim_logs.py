#!/usr/bin/env python3
"""Sprint 9: ingest rosbag / sim / URDF / Foxglove logs into know.

End-to-end driver that exercises every Sprint 9 adapter on one or more
sources and emits:

* ``stdout`` — markdown summary (FailureMode counts + cross-embodiment
  reuse table);
* ``--out`` JSON — full :class:`MappedFailure` + :class:`URDFDoc` dump
  for downstream :mod:`graph_builder_v2` and :mod:`bridge_reweighter`
  consumers.

Plan §Sprint 9 acceptance gates are evaluated automatically; the
script exits 1 if neither failure-reuse nor pattern-reuse gate passes
on the supplied inputs (so CI can require some cross-embodiment
evidence before merging a new ingest config).

Example::

    python scripts/ingest_sim_logs.py \\
      --rosbag tests/fixtures/sprint9/sample.rosbag.jsonl \\
      --isaac  tests/fixtures/sprint9/sample_isaac.jsonl \\
      --mujoco tests/fixtures/sprint9/sample_mujoco.jsonl \\
      --foxglove tests/fixtures/sprint9/sample_foxglove.json \\
      --urdf   tests/fixtures/sprint9/ur5.urdf \\
      --controller-config tests/fixtures/sprint9/controller_config.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from rosclaw_know.sim_ingest import (
    map_events_to_failures,
    parse_controller_config,
    parse_urdf,
    read_foxglove_jsonl,
    read_isaac_jsonl,
    read_mujoco_jsonl,
    read_rosbag_jsonl,
    run_cross_embodiment_check,
    urdf_to_constraints,
    urdf_to_embodiment,
)
from rosclaw_know.sim_ingest.cross_embodiment import render_markdown as render_xemb_md

log = logging.getLogger("ingest_sim_logs")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sprint 9: ingest rosbag/sim/URDF logs into know.",
    )
    p.add_argument("--rosbag", action="append", default=[],
                   help="Path to rosbag-JSONL.  Repeatable.")
    p.add_argument("--isaac",  action="append", default=[],
                   help="Path to Isaac Sim rollout JSONL.  Repeatable.")
    p.add_argument("--mujoco", action="append", default=[],
                   help="Path to MuJoCo rollout JSONL.  Repeatable.")
    p.add_argument("--foxglove", action="append", default=[],
                   help="Path to Foxglove annotation export.  Repeatable.")
    p.add_argument("--urdf", action="append", default=[],
                   help="Path to URDF document.  Repeatable.")
    p.add_argument("--controller-config", default=None,
                   help="Optional ros2_control YAML applied to the LAST --urdf.")
    p.add_argument("--out", default=None,
                   help="Write JSON dump of every artifact to this path.")
    p.add_argument("--allow-failures", action="store_true",
                   help="Exit 0 even if no acceptance gate passes.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _read_events(args: argparse.Namespace) -> list:
    out: list = []
    for path in args.rosbag:
        evs = read_rosbag_jsonl(path)
        print(f"  rosbag   {path}: {len(evs)} events", file=sys.stderr)
        out.extend(evs)
    for path in args.isaac:
        evs = read_isaac_jsonl(path)
        print(f"  isaac    {path}: {len(evs)} events", file=sys.stderr)
        out.extend(evs)
    for path in args.mujoco:
        evs = read_mujoco_jsonl(path)
        print(f"  mujoco   {path}: {len(evs)} events", file=sys.stderr)
        out.extend(evs)
    for path in args.foxglove:
        evs = read_foxglove_jsonl(path)
        print(f"  foxglove {path}: {len(evs)} events", file=sys.stderr)
        out.extend(evs)
    return out


def _process_urdfs(args: argparse.Namespace) -> tuple[list, list]:
    embodiments: list = []
    constraints: list = []
    ctrl = None
    if args.controller_config:
        ctrl = parse_controller_config(args.controller_config)
    for path in args.urdf:
        doc = parse_urdf(path)
        emb = urdf_to_embodiment(doc, controller=ctrl)
        cons = urdf_to_constraints(doc, controller=ctrl)
        print(
            f"  urdf     {path}: {len(doc.joints)} joints → "
            f"1 embodiment + {len(cons)} constraints",
            file=sys.stderr,
        )
        embodiments.append((doc, emb))
        constraints.extend(cons)
    return embodiments, constraints


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not any([args.rosbag, args.isaac, args.mujoco, args.foxglove, args.urdf]):
        print("ERROR: provide at least one of --rosbag/--isaac/--mujoco/--foxglove/--urdf",
              file=sys.stderr)
        return 2

    print("Reading sources…", file=sys.stderr)
    events = _read_events(args)
    urdf_pairs, constraints = _process_urdfs(args)

    print(f"  → {len(events)} total events", file=sys.stderr)

    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)

    # ── stdout summary ──────────────────────────────────────────────────
    print("# Sprint 9 ingest summary")
    print()
    print(f"- adapters fired: rosbag={len(args.rosbag)}, "
          f"isaac={len(args.isaac)}, mujoco={len(args.mujoco)}, "
          f"foxglove={len(args.foxglove)}")
    print(f"- urdf files parsed: {len(urdf_pairs)}")
    print(f"- total events: {len(events)}")
    print(f"- distinct FailureMode: {len(failures)}")
    print(f"- distinct embodiments observed: {len(report.distinct_embodiments)}")
    print(f"- ConstraintPattern emitted: {len(constraints)}")
    print()
    print("## FailureMode (id · domain · severity · #occurrences · embodiments)")
    print()
    print("| id | domain | severity | n | embodiments |")
    print("|---|---|---|---:|---|")
    for mf in failures:
        embs = ", ".join(mf.embodiments_seen)
        print(f"| {mf.failure.id} | {mf.failure.domain} | "
              f"{mf.failure.severity} | {mf.occurrence_count} | {embs} |")
    print()
    print(render_xemb_md(report))

    # ── JSON dump ───────────────────────────────────────────────────────
    if args.out:
        out_path = Path(args.out)
        payload = {
            "schema_version": "1.0",
            "events_count": len(events),
            "failures": [
                {
                    "id": mf.failure.id,
                    "name": mf.failure.name,
                    "domain": mf.failure.domain,
                    "severity": mf.failure.severity,
                    "observable_signals": mf.failure.observable_signals,
                    "likely_causes": mf.failure.likely_causes,
                    "contraindications": mf.failure.contraindications,
                    "occurrence_count": mf.occurrence_count,
                    "embodiments_seen": list(mf.embodiments_seen),
                }
                for mf in failures
            ],
            "embodiments": [
                {
                    "id": emb.id,
                    "embodiment_type": emb.embodiment_type,
                    "actuators": emb.actuators,
                    "sensors": emb.sensors,
                    "control_interfaces": emb.control_interfaces,
                    "safety_constraints": emb.safety_constraints,
                }
                for _doc, emb in urdf_pairs
            ],
            "constraints": [
                {
                    "id": c.id,
                    "constraint_type": c.constraint_type,
                    "description": c.description,
                    "violation_signals": c.violation_signals,
                }
                for c in constraints
            ],
            "cross_embodiment": {
                "distinct_embodiments": list(report.distinct_embodiments),
                "failures_seen_on_multiple_embodiments": [
                    mf.failure.id for mf in report.failures_seen_on_multiple_embodiments
                ],
                "patterns_seen_on_multiple_embodiments": [
                    {
                        "pattern_id": row.pattern_id,
                        "event_types": list(row.failure_event_types),
                        "embodiments": list(row.embodiments),
                    }
                    for row in report.patterns_seen_on_multiple_embodiments
                ],
                "all_pattern_rows": [
                    {
                        "pattern_id": row.pattern_id,
                        "event_types": list(row.failure_event_types),
                        "embodiments": list(row.embodiments),
                    }
                    for row in report.all_pattern_rows
                ],
                "notes": list(report.notes),
                "acceptance_pattern_reuse_passed":
                    report.acceptance_pattern_reuse_passed,
                "acceptance_failure_reuse_passed":
                    report.acceptance_failure_reuse_passed,
            },
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out_path)
        print(f"\nWrote {out_path}", file=sys.stderr)

    # ── acceptance ──────────────────────────────────────────────────────
    if not args.allow_failures:
        if not (report.acceptance_pattern_reuse_passed
                or report.acceptance_failure_reuse_passed):
            print("\n[FAIL] Sprint 9 acceptance: no cross-embodiment reuse evidence.",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
