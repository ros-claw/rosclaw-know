#!/usr/bin/env python3
"""Phase 9 real-agent A/B harness runner.

Drives a configurable agent (synthetic / LLM / Claude) over the tasks in
``data/eval_tasks/*.yaml`` and produces a reproducible benchmark report under
``data/benchmarks/phase9_real_agent/<label>/``.

Example::

    python scripts/agent_eval_runner.py \
      --backend synthetic --seeds 30 --label p9_smoke

Exit codes:
  0 — harness ran and passed Phase 9 acceptance gates.
  1 — harness ran but acceptance gates failed.
  2 — bad arguments / no tasks / backend unavailable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know import config  # noqa: E402
from rosclaw_know.ab_harness import (  # noqa: E402
    ALL_ARMS,
    TaskRunResult,
    TaskSpec,
    paired_trend_p_value,
    post_injection_deltas,
    run_matrix,
)
from rosclaw_know.agent_eval.backends import build_backend  # noqa: E402
from rosclaw_know.agent_eval.report_writer import write_report  # noqa: E402
from rosclaw_know.agent_eval.task_loader import load_tasks  # noqa: E402
from rosclaw_know.agent_eval.task_runner import run_one_with_code  # noqa: E402
from rosclaw_know.agent_eval.types import EvalTask  # noqa: E402

log = logging.getLogger("agent_eval_runner")


def _to_task_spec(task: EvalTask) -> TaskSpec:
    """Map an eval task to the ab_harness shape."""
    return TaskSpec(
        task_id=task.task_id,
        objective_direction=task.objective_direction,  # type: ignore[arg-type]
        metric_name=task.metric_name,
    )


def _acceptance_report(results: list[TaskRunResult]) -> dict[str, Any]:
    """Compute Phase 9 pass/fail metrics for true_know vs baseline."""
    deltas = post_injection_deltas(results, "true_know")
    pvals = paired_trend_p_value(results, "true_know")

    tasks_with_data = [t for t, d in deltas.items() if d is not None]
    positive = [t for t in tasks_with_data if deltas[t] is not None and deltas[t] > 0]
    significant_positive = [
        t
        for t in tasks_with_data
        if pvals.get(t) is not None and pvals[t] < 0.1 and (deltas[t] or 0) > 0
    ]
    significant_negative = [
        t
        for t in tasks_with_data
        if pvals.get(t) is not None and pvals[t] < 0.1 and (deltas[t] or 0) < 0
    ]
    avg_delta = (
        sum(d for d in deltas.values() if d is not None) / len(tasks_with_data)
        if tasks_with_data
        else 0.0
    )

    n_tasks = len(tasks_with_data)
    n_seeds = len({r.seed for r in results})
    # With fewer than 3 seeds p-values are unreliable; fall back to positive-delta gate.
    if n_seeds < 3:
        min_significant = 1
        passed = avg_delta >= 0.10 and not significant_negative and len(positive) == n_tasks
    else:
        min_significant = max(3, n_tasks // 2)
        passed = (
            len(significant_positive) >= min_significant
            and avg_delta >= 0.10
            and not significant_negative
        )

    return {
        "n_tasks": n_tasks,
        "positive_tasks": positive,
        "significant_positive_tasks": significant_positive,
        "significant_negative_tasks": significant_negative,
        "avg_true_know_delta": round(avg_delta, 4),
        "min_significant_threshold": min_significant,
        "passed": passed,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tasks",
        default="data/eval_tasks/*.yaml",
        help="Glob pattern for task YAML files. Default: data/eval_tasks/*.yaml",
    )
    p.add_argument(
        "--backend",
        choices=("synthetic", "llm", "claude"),
        default="synthetic",
        help="Agent backend. Default: synthetic",
    )
    p.add_argument(
        "--seeds",
        type=int,
        default=30,
        help="Number of random seeds per (task, arm). Default: 30",
    )
    p.add_argument(
        "--label",
        required=True,
        help="Run label; used as the output directory name.",
    )
    p.add_argument(
        "--arms",
        nargs="+",
        choices=ALL_ARMS,
        default=list(ALL_ARMS),
        help="Which arms to run. Default: all six Sprint 8 arms.",
    )
    p.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name for LLM backend. Defaults to DEEPSEEK_MUSE_MODEL env var.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.3,
        help="Sampling temperature for LLM backends. Default: 0.3",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-trial agent-code execution timeout in seconds. Default: 5.0",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=4000,
        help="Max tokens for LLM code generation. Reasoning models often need >=4000. Default: 4000",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    config.ensure_dirs()

    try:
        eval_tasks = load_tasks(args.tasks)
    except (FileNotFoundError, ValueError) as exc:
        log.error("failed to load tasks: %s", exc)
        return 2

    try:
        backend = build_backend(
            args.backend,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except ValueError as exc:
        log.error("bad backend: %s", exc)
        return 2

    task_specs = [_to_task_spec(t) for t in eval_tasks]
    seeds = list(range(1, args.seeds + 1))
    arms = list(args.arms)

    log.info(
        "Running %d tasks x %d arms x %d seeds = %d trials (backend=%s)",
        len(eval_tasks),
        len(arms),
        len(seeds),
        len(eval_tasks) * len(arms) * len(seeds),
        args.backend,
    )

    codes: dict[tuple[str, str, int], str] = {}

    def _run_fn(task_spec: TaskSpec, arm: str, seed: int) -> TaskRunResult:
        task = next(t for t in eval_tasks if t.task_id == task_spec.task_id)
        result, code = run_one_with_code(task, backend, arm, seed)  # type: ignore[arg-type]
        codes[(task.task_id, arm, seed)] = code
        return result

    results = run_matrix(task_specs, arms, seeds, _run_fn)

    out_dir = write_report(args.label, results, codes)
    log.info("Wrote report to %s", out_dir)

    report = _acceptance_report(results)
    log.info(
        "Phase 9 acceptance — avg Δ=%s, significant positive=%d/%d, "
        "significant negative=%s, passed=%s",
        report["avg_true_know_delta"],
        len(report["significant_positive_tasks"]),
        report["n_tasks"],
        report["significant_negative_tasks"],
        report["passed"],
    )

    if not report["passed"]:
        log.error("Phase 9 acceptance gates FAILED")
        return 1
    log.info("Phase 9 acceptance gates PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
