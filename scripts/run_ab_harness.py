#!/usr/bin/env python3
"""Sprint 8: run the 6-arm A/B harness (plan §Sprint 8).

Runs the 10 representative Frontier-Eng tasks listed in the plan
across all six arms × ``--seeds``-many seeds.  Default backend is the
deterministic synthetic ``run_fn`` from :mod:`ab_synthetic`; pass
``--backend external`` and point ``--run-fn`` at an importable name to
swap in a real Frontier-Eng wrapper.

Outputs:
  * stdout — compact markdown summary
  * ``data/assets/ab_reports/sprint8_<tag>.json`` — full JSON report
  * ``data/assets/ab_reports/sprint8_<tag>.md``   — markdown copy

Exits 0 if all acceptance gates pass, 1 otherwise (so CI can gate on it).
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path

from rosclaw_know import config
from rosclaw_know.ab_harness import (
    ALL_ARMS,
    TaskSpec,
    acceptance_report,
    render_markdown,
    run_matrix,
    to_jsonable,
)
from rosclaw_know.ab_synthetic import synthetic_run_fn

logger = logging.getLogger("run_ab_harness")


# Plan §Sprint 8: 10 representative tasks with their objective
# directions and primary metric names.  Names match the TaskCards in
# data/assets/task_cards.yaml where they exist; the rest are
# placeholder ids the synthetic backend understands.
_PLAN_TASKS: list[TaskSpec] = [
    TaskSpec("pid_tuning",                   "minimize", "itae"),
    TaskSpec("crypto_aes128",                "maximize", "throughput"),
    TaskSpec("flash_attention",              "maximize", "tokens_per_sec"),
    TaskSpec("high_reliable_simulation",     "maximize", "reliability"),
    TaskSpec("quadruped_gait",               "maximize", "velocity"),
    TaskSpec("robot_arm_cycle_time",         "minimize", "cycle_time"),
    TaskSpec("battery_fast_charging",        "minimize", "time_to_full"),
    TaskSpec("jobshop_abz",                  "minimize", "makespan"),
    TaskSpec("topology_optimization",        "maximize", "stiffness"),
    TaskSpec("uav_inspection",               "maximize", "coverage"),
]


def _resolve_external_run_fn(spec: str):
    """Import a ``module.attr`` style identifier as the run_fn."""
    if ":" in spec:
        module_name, attr = spec.split(":", 1)
    elif "." in spec:
        module_name, attr = spec.rsplit(".", 1)
    else:
        raise ValueError(
            f"--run-fn must be 'module.attr' or 'module:attr', got {spec!r}"
        )
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sprint 8: 6-arm A/B harness (plan §Sprint 8 reference run).",
    )
    p.add_argument(
        "--seeds", type=int, default=3,
        help="Number of seeds per (task, arm).  Default 3.",
    )
    p.add_argument(
        "--backend", choices=("synthetic", "external"), default="synthetic",
        help=(
            "synthetic = run with the deterministic in-repo backend "
            "(default; used by CI).  external = load run_fn via --run-fn."
        ),
    )
    p.add_argument(
        "--run-fn", default=None,
        help=(
            "External run_fn identifier (e.g. "
            "'frontier_eng_wrapper:run_one').  Only with --backend external."
        ),
    )
    p.add_argument(
        "--out-tag", default="synthetic",
        help="Tag included in the output filenames.  Default 'synthetic'.",
    )
    p.add_argument(
        "--out-dir",
        default=str(config.ASSETS_DIR / "ab_reports"),
        help="Output directory.  Default data/assets/ab_reports/.",
    )
    p.add_argument(
        "--allow-failures", action="store_true",
        help="Exit 0 even when an acceptance gate fails.",
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

    if args.backend == "synthetic":
        run_fn = synthetic_run_fn
    else:
        if not args.run_fn:
            print(
                "ERROR: --backend external requires --run-fn",
                file=sys.stderr,
            )
            return 2
        run_fn = _resolve_external_run_fn(args.run_fn)

    tasks = list(_PLAN_TASKS)
    arms = list(ALL_ARMS)
    seeds = list(range(1, args.seeds + 1))

    print(
        f"Running {len(tasks)} tasks × {len(arms)} arms × {len(seeds)} seeds "
        f"= {len(tasks) * len(arms) * len(seeds)} trials "
        f"(backend={args.backend})",
        file=sys.stderr,
    )
    results = run_matrix(tasks, arms, seeds, run_fn)
    print(f"  {len(results)} results returned.", file=sys.stderr)

    # Render summary.
    md = render_markdown(results, arms=arms)
    print(md)

    payload = to_jsonable(results, arms=arms)

    # Persist.
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = args.out_tag.replace("/", "_")
    json_path = out_dir / f"sprint8_{tag}.json"
    md_path = out_dir / f"sprint8_{tag}.md"
    tmp_json = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp_md = md_path.with_suffix(md_path.suffix + ".tmp")
    tmp_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_md.write_text(md, encoding="utf-8")
    tmp_json.replace(json_path)
    tmp_md.replace(md_path)
    print(f"\nWrote {json_path}", file=sys.stderr)
    print(f"Wrote {md_path}", file=sys.stderr)

    report = acceptance_report(results, arms=arms)
    if not report.all_passed and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
