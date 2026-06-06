#!/usr/bin/env python3
"""Aggregate multi-seed Frontier-Eng A/B results.

Given a base dir containing ``seed_1/``, ``seed_2/``, ... subdirs (each
populated by ``verify_frontier_eng.py --out-dir <seed_dir>``), this script:

1. Invokes ``judge_frontier_eng.py`` on each seed dir to score control vs
   treatment for that seed (writes scores back into each
   ``seed_*/summary.json``).
2. Reads all N seed summary.json files and computes:
     - Per-seed avg uplift, win rate, verdict counts
     - Across-seed mean uplift ± 95% CI (normal approx, N=5 so wide)
     - Per-task uplift mean ± std across seeds (so we can see *which* tasks
       are robustly improved vs. just bouncing with judge noise)

Multi-seed only makes sense when verify ran at ``--temperature > 0`` — at
temp 0 the model output is near-deterministic and the 5 seeds collapse to
one sample.  We don't enforce that here, just warn loudly.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import BENCHMARKS_DIR  # noqa: E402

JUDGE_SCRIPT = PROJECT_ROOT / "scripts" / "judge_frontier_eng.py"


def _judge_seed(seed_dir: Path) -> int:
    """Run judge_frontier_eng on a single seed dir.  Returns judge's exit code."""
    print(f"  ↳ judging {seed_dir.name}…", flush=True)
    res = subprocess.run(
        [sys.executable, str(JUDGE_SCRIPT), "--report-dir", str(seed_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        print(f"    judge stderr: {res.stderr[:400]}", file=sys.stderr)
    return res.returncode


def _load_seed_summary(seed_dir: Path) -> list[dict]:
    p = seed_dir / "summary.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def _ci95(values: list[float]) -> tuple[float, float]:
    """95% normal-approx confidence interval for the mean (low N, use t-dist)."""
    n = len(values)
    if n < 2:
        return (float("nan"), float("nan"))
    mean = statistics.mean(values)
    sd = statistics.stdev(values)
    # t-score for 95% two-tail at df=n-1; hand-coded for n in [2..10]
    t_table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}
    t = t_table.get(n - 1, 2.0)
    margin = t * sd / math.sqrt(n)
    return (mean - margin, mean + margin)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--base-dir",
        type=Path,
        default=BENCHMARKS_DIR / "multiseed",
        help="Directory containing seed_1/, seed_2/, ... subdirs from verify_frontier_eng.",
    )
    ap.add_argument("--skip-judge", action="store_true",
                    help="Don't re-run judge; assume each seed's summary.json already has judgments.")
    args = ap.parse_args()

    seed_dirs = sorted(
        (p for p in args.base_dir.glob("seed_*") if p.is_dir()),
        key=lambda p: (len(p.name), p.name),
    )
    if not seed_dirs:
        print(f"No seed_*/ subdirs in {args.base_dir}", file=sys.stderr)
        return 1
    print(f"Found {len(seed_dirs)} seed dirs.")

    if not args.skip_judge:
        print("\n=== Judging each seed ===")
        for sd in seed_dirs:
            _judge_seed(sd)

    # ── Collect (task_id, seed_idx) → control, treatment, uplift, verdict ──
    print("\n=== Loading judged summaries ===")
    per_task_uplifts: dict[str, list[int]] = {}
    per_task_verdicts: dict[str, list[str]] = {}
    per_seed_avg_uplift: list[float] = []
    per_seed_win_rate: list[float] = []
    per_seed_verdicts: list[dict[str, int]] = []

    for sd in seed_dirs:
        summary = _load_seed_summary(sd)
        if not summary:
            print(f"  ! {sd.name}: empty/missing summary.json")
            continue
        deltas: list[int] = []
        v_counts = {"treatment_better": 0, "control_better": 0, "tie": 0, "skipped": 0}
        for entry in summary:
            j = entry.get("judgment", {})
            c = j.get("control", {}).get("score")
            t = j.get("treatment", {}).get("score")
            verdict = j.get("verdict", "skipped")
            v_counts[verdict] = v_counts.get(verdict, 0) + 1
            if c is None or t is None:
                continue
            d = t - c
            deltas.append(d)
            tid = entry["task_id"]
            per_task_uplifts.setdefault(tid, []).append(d)
            per_task_verdicts.setdefault(tid, []).append(verdict)
        if not deltas:
            continue
        avg = statistics.mean(deltas)
        wr = v_counts.get("treatment_better", 0) / len(deltas)
        per_seed_avg_uplift.append(avg)
        per_seed_win_rate.append(wr)
        per_seed_verdicts.append(v_counts)
        print(f"  {sd.name}: avg_uplift={avg:+.2f}  win_rate={wr:.0%}  "
              f"({v_counts['treatment_better']}T / {v_counts['control_better']}C / {v_counts['tie']}=)")

    if not per_seed_avg_uplift:
        print("No usable seed data.", file=sys.stderr)
        return 1

    # ── Per-task across-seed stats ──
    print("\n=== Per-task uplift across seeds (mean ± std) ===")
    print(f"{'task_id':<32} {'mean Δ':>8} {'std Δ':>7} {'n':>3}  verdicts")
    for tid in sorted(per_task_uplifts):
        ups = per_task_uplifts[tid]
        verds = per_task_verdicts[tid]
        mean = statistics.mean(ups)
        sd = statistics.stdev(ups) if len(ups) > 1 else 0.0
        v_str = "/".join([
            f"{verds.count('treatment_better')}T",
            f"{verds.count('control_better')}C",
            f"{verds.count('tie')}=",
        ])
        print(f"{tid:<32} {mean:+8.2f} {sd:7.2f} {len(ups):>3}  {v_str}")

    # ── Across-seed aggregate ──
    print("\n=== Across-seed aggregate ===")
    n = len(per_seed_avg_uplift)
    mean_uplift = statistics.mean(per_seed_avg_uplift)
    sd_uplift = statistics.stdev(per_seed_avg_uplift) if n > 1 else 0.0
    ci_lo, ci_hi = _ci95(per_seed_avg_uplift)
    mean_wr = statistics.mean(per_seed_win_rate)
    ci_lo_wr, ci_hi_wr = _ci95(per_seed_win_rate)

    print(f"Seeds:                {n}")
    print(f"Per-seed avg uplift:  [{', '.join(f'{u:+.2f}' for u in per_seed_avg_uplift)}]")
    print(f"Across-seed mean Δ:   {mean_uplift:+.3f}   std={sd_uplift:.3f}   "
          f"95% CI [{ci_lo:+.2f}, {ci_hi:+.2f}]")
    print(f"Per-seed win rate:    [{', '.join(f'{w:.0%}' for w in per_seed_win_rate)}]")
    print(f"Across-seed mean WR:  {mean_wr:.1%}   95% CI [{ci_lo_wr:.0%}, {ci_hi_wr:.0%}]")

    # ── Per-panel sub-split (if benchmark mixes "wild" TASK_NNN and
    # "home-turf" TASK_W_NNN — the prefix is enough to bucket) ──
    home_turf_tids = [t for t in per_task_uplifts if "_W_" in t]
    if home_turf_tids and len(home_turf_tids) < len(per_task_uplifts):
        print("\n=== Per-panel split (wild TASK_NNN vs home-turf TASK_W_NNN) ===")
        for panel_name, predicate in (
            ("wild", lambda t: "_W_" not in t),
            ("home-turf", lambda t: "_W_" in t),
        ):
            panel_tids = [t for t in per_task_uplifts if predicate(t)]
            if not panel_tids:
                continue
            # Re-run per-seed aggregate over the panel subset.
            seed_avgs: list[float] = []
            seed_wrs: list[float] = []
            for sd in seed_dirs:
                summary = _load_seed_summary(sd)
                deltas: list[int] = []
                trt = 0
                for entry in summary:
                    if not predicate(entry["task_id"]):
                        continue
                    j = entry.get("judgment", {})
                    c = j.get("control", {}).get("score")
                    t = j.get("treatment", {}).get("score")
                    if c is None or t is None:
                        continue
                    deltas.append(t - c)
                    if j.get("verdict") == "treatment_better":
                        trt += 1
                if deltas:
                    seed_avgs.append(statistics.mean(deltas))
                    seed_wrs.append(trt / len(deltas))
            if not seed_avgs:
                continue
            n_panel = len(seed_avgs)
            m = statistics.mean(seed_avgs)
            sd_v = statistics.stdev(seed_avgs) if n_panel > 1 else 0.0
            lo, hi = _ci95(seed_avgs)
            wr_m = statistics.mean(seed_wrs)
            lo_w, hi_w = _ci95(seed_wrs)
            print(f"  panel={panel_name:<10}  tasks={len(panel_tids):>2}  "
                  f"seeds={n_panel}  mean Δ={m:+.3f} std={sd_v:.3f}  "
                  f"CI [{lo:+.2f}, {hi:+.2f}]  WR={wr_m:.0%} CI [{lo_w:.0%}, {hi_w:.0%}]")
        print("(home-turf = bridge has a relevant curated/muse pattern by construction;")
        print(" wild = realistic distribution including cold-coverage domains. "
              "§5.6 phase-1 acceptance: WR ≥ 55% AND mean Δ > 0.)")

    # ── Verdict against the -0.80 single-seed temp-0 baseline ──
    print("\n=== Verdict vs single-seed temp-0 baselines ===")
    print(f"Pre-fix  (baseline, temp 0, 1 seed):   avg uplift = -0.80")
    print(f"Post-fix (e2bd61d, temp 0, 1 seed):    avg uplift = +0.20")
    print(f"Post-fix (e2bd61d, temp 0.3, {n} seeds): avg uplift = {mean_uplift:+.2f} "
          f"95% CI [{ci_lo:+.2f}, {ci_hi:+.2f}]")
    if ci_lo > 0:
        print("→ CI is entirely above 0: snippet-reorder shows STATISTICALLY POSITIVE uplift.")
    elif ci_hi < 0:
        print("→ CI is entirely below 0: snippet-reorder shows STATISTICALLY NEGATIVE uplift (regression).")
    else:
        print("→ CI straddles 0: with this many seeds we can't claim significance; need more samples.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
