"""Sprint 8: strict 6-arm A/B harness (plan §Sprint 8).

Verifies whether ROSClaw-Know **causally** helps an agent on
Frontier-Engineering tasks.  Compares six arms:

    A. baseline            — agent runs with no hint
    B. true_know           — hint pulled from the real bridge_index
    C. placebo_know        — hint is a curated-shape *but unrelated* text
    D. shuffled_know       — hint pulled from a wrong task family
    E. task_pack_only      — Sprint-7 TaskPack provided pre-flight, no CATALYST
    F. task_pack_plus_catalyst — TaskPack pre-flight + CATALYST when stuck

The module is **pure framework** — no Frontier-Eng dependency.  Callers
plug in a ``run_fn(task, arm, seed) -> TaskRunResult`` callback.  This
keeps the harness testable in CI (via the synthetic backend in
:mod:`ab_synthetic`) and lets production deploys swap in a real
Frontier-Eng wrapper without modifying the analysis code.

Following Frontier-Eng's official rubric: heterogeneous tasks are not
mixed by raw score — every metric here is rank-based or
ratio-of-best (performance-profile style).  See plan §Sprint 8
("Frontier-Eng 官方也强调异构任务不能直接混原始分数").
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

log = logging.getLogger("rosclaw_know.ab_harness")


# ── arms ────────────────────────────────────────────────────────────────


ArmName = Literal[
    "baseline",
    "true_know",
    "placebo_know",
    "shuffled_know",
    "task_pack_only",
    "task_pack_plus_catalyst",
]
ALL_ARMS: tuple[ArmName, ...] = (
    "baseline",
    "true_know",
    "placebo_know",
    "shuffled_know",
    "task_pack_only",
    "task_pack_plus_catalyst",
)
ObjectiveDirection = Literal["maximize", "minimize"]


# ── input + output shapes ───────────────────────────────────────────────


@dataclass(frozen=True)
class TaskSpec:
    """One task in the matrix."""

    task_id: str
    objective_direction: ObjectiveDirection = "maximize"
    metric_name: str = "score"


@dataclass(frozen=True)
class TaskRunResult:
    """Outcome of one (task, arm, seed) trial."""

    task_id: str
    arm: ArmName
    seed: int
    score: float | None
    """``None`` when the run was invalid (crash, NaN, OOM); excluded from
    rank computation but counted in ``validity_preservation_rate``."""
    objective_direction: ObjectiveDirection = "maximize"
    valid: bool = True
    hint_use_rate: float = 0.0
    """Per-run hint adoption signal — set 0 for arms that don't carry a
    hint (baseline / task_pack_only)."""


RunFn = Callable[[TaskSpec, ArmName, int], TaskRunResult]


# ── core runner ─────────────────────────────────────────────────────────


def run_matrix(
    tasks: Sequence[TaskSpec],
    arms: Sequence[ArmName],
    seeds: Sequence[int],
    run_fn: RunFn,
) -> list[TaskRunResult]:
    """Cartesian sweep over (task × arm × seed); return all results.

    No parallelism here — the run_fn is expected to be the slow piece
    (real Frontier-Eng runs).  Callers that want concurrency should
    wrap run_fn before passing it in.
    """
    out: list[TaskRunResult] = []
    for task in tasks:
        for arm in arms:
            for seed in seeds:
                result = run_fn(task, arm, seed)
                out.append(result)
    return out


# ── per-(task, arm) aggregates ──────────────────────────────────────────


@dataclass(frozen=True)
class TaskArmSummary:
    """Aggregated stats for one (task, arm) cell."""

    task_id: str
    arm: ArmName
    objective_direction: ObjectiveDirection
    n: int
    n_valid: int
    mean_score: float | None
    std_score: float | None
    validity_preservation_rate: float
    mean_hint_use_rate: float


def _aggregate_cells(
    results: Sequence[TaskRunResult],
) -> dict[tuple[str, ArmName], TaskArmSummary]:
    """Group results by (task, arm) and compute means + validity rates."""
    bucket: dict[tuple[str, ArmName], list[TaskRunResult]] = defaultdict(list)
    for r in results:
        bucket[(r.task_id, r.arm)].append(r)
    out: dict[tuple[str, ArmName], TaskArmSummary] = {}
    for key, rs in bucket.items():
        valid_scores = [r.score for r in rs if r.valid and r.score is not None]
        n_valid = len(valid_scores)
        mean = round(statistics.fmean(valid_scores), 6) if n_valid else None
        std = round(statistics.stdev(valid_scores), 6) if n_valid > 1 else None
        validity_rate = sum(1 for r in rs if r.valid) / len(rs)
        hint_use = round(statistics.fmean(r.hint_use_rate for r in rs), 4) if rs else 0.0
        out[key] = TaskArmSummary(
            task_id=key[0],
            arm=key[1],
            objective_direction=rs[0].objective_direction,
            n=len(rs),
            n_valid=n_valid,
            mean_score=mean,
            std_score=std,
            validity_preservation_rate=round(validity_rate, 4),
            mean_hint_use_rate=hint_use,
        )
    return out


# ── per-task arm ranking ────────────────────────────────────────────────


def _rank_arms_within_task(
    cells: dict[tuple[str, ArmName], TaskArmSummary],
    task_id: str,
    arms: Sequence[ArmName],
) -> dict[ArmName, float]:
    """Average-rank arms within one task.

    Arms with no valid runs get the worst-possible rank (= number of
    arms with valid data + 1).  Ties get fractional ranks
    (e.g. ``[1, 2.5, 2.5, 4]``) — same convention scipy uses.
    """
    summaries = [cells.get((task_id, a)) for a in arms]
    direction = next(
        (s.objective_direction for s in summaries if s is not None),
        "maximize",
    )
    # Pair (arm, score) for arms with a score; collect "missing" arms
    # separately so we can park them at the bottom.
    scored: list[tuple[ArmName, float]] = []
    missing: list[ArmName] = []
    for arm, s in zip(arms, summaries):
        if s is None or s.mean_score is None:
            missing.append(arm)
        else:
            scored.append((arm, s.mean_score))
    # Sort with the direction in mind — "best" comes first.
    scored.sort(
        key=lambda t: t[1],
        reverse=(direction == "maximize"),
    )
    ranks: dict[ArmName, float] = {}
    i = 0
    while i < len(scored):
        # Find tie group.
        j = i
        while j < len(scored) and scored[j][1] == scored[i][1]:
            j += 1
        # Average rank of positions [i+1, j].
        avg_rank = sum(range(i + 1, j + 1)) / (j - i)
        for k in range(i, j):
            ranks[scored[k][0]] = avg_rank
        i = j
    # Missing arms get the post-last position (uniform).
    fallback_rank = len(scored) + 1
    for arm in missing:
        ranks[arm] = float(fallback_rank)
    return ranks


# ── per-arm aggregates ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ArmSummary:
    """Cross-task aggregate for one arm."""

    arm: ArmName
    avg_rank: float
    """Plan §Sprint 8 primary metric — lower is better.  Rank-based so
    we don't pretend heterogeneous tasks are comparable on raw score."""
    n_tasks: int
    n_tasks_with_data: int
    validity_preservation_rate: float
    mean_hint_use_rate: float
    """Mean hint adoption — 0 for arms without hints by design."""
    win_rate_vs_baseline: float
    """Fraction of tasks where this arm's mean strictly beats baseline."""
    avg_post_injection_delta_vs_baseline: float
    """Mean of (arm.mean − baseline.mean) signed by direction — positive =
    arm is doing better than baseline."""


def compute_arm_summaries(
    results: Sequence[TaskRunResult],
    arms: Sequence[ArmName] = ALL_ARMS,
) -> dict[ArmName, ArmSummary]:
    """Top-level cross-task aggregates."""
    cells = _aggregate_cells(results)
    tasks = sorted({r.task_id for r in results})
    direction_by_task = {r.task_id: r.objective_direction for r in results}

    # Rank per task.
    per_task_ranks: dict[str, dict[ArmName, float]] = {
        t: _rank_arms_within_task(cells, t, arms) for t in tasks
    }

    out: dict[ArmName, ArmSummary] = {}
    for arm in arms:
        ranks = [per_task_ranks[t][arm] for t in tasks if arm in per_task_ranks[t]]
        # Tasks with data on this arm
        with_data = [
            t
            for t in tasks
            if (cells.get((t, arm)) is not None and cells[(t, arm)].mean_score is not None)
        ]
        # Win rate vs baseline
        wins = 0
        deltas: list[float] = []
        for t in tasks:
            arm_cell = cells.get((t, arm))
            base_cell = cells.get((t, "baseline"))
            if (
                arm_cell is None
                or base_cell is None
                or arm_cell.mean_score is None
                or base_cell.mean_score is None
            ):
                continue
            direction = direction_by_task[t]
            arm_s = arm_cell.mean_score
            base_s = base_cell.mean_score
            if direction == "maximize":
                delta = arm_s - base_s
                if arm_s > base_s:
                    wins += 1
            else:
                delta = base_s - arm_s
                if arm_s < base_s:
                    wins += 1
            deltas.append(delta)
        denom = max(len(deltas), 1)
        # Validity rate over all (task, seed) trials.
        arm_results = [r for r in results if r.arm == arm]
        valid_n = sum(1 for r in arm_results if r.valid)
        validity_rate = valid_n / len(arm_results) if arm_results else 0.0
        # Mean hint_use_rate
        hint_use = statistics.fmean(r.hint_use_rate for r in arm_results) if arm_results else 0.0
        out[arm] = ArmSummary(
            arm=arm,
            avg_rank=round(statistics.fmean(ranks), 4) if ranks else float("inf"),
            n_tasks=len(tasks),
            n_tasks_with_data=len(with_data),
            validity_preservation_rate=round(validity_rate, 4),
            mean_hint_use_rate=round(hint_use, 4),
            win_rate_vs_baseline=round(wins / denom, 4),
            avg_post_injection_delta_vs_baseline=round(statistics.fmean(deltas), 6)
            if deltas
            else 0.0,
        )
    return out


# ── pairwise win rate ──────────────────────────────────────────────────


def pairwise_win_rate(
    results: Sequence[TaskRunResult],
    arm_a: ArmName,
    arm_b: ArmName,
) -> float:
    """Fraction of tasks where arm_a's mean score strictly beats arm_b.

    Direction-aware: for "minimize" tasks, lower score is the win.
    Tasks where either arm has no valid data are excluded from the
    denominator.
    """
    cells = _aggregate_cells(results)
    tasks = sorted({r.task_id for r in results})
    direction_by_task = {r.task_id: r.objective_direction for r in results}
    n = 0
    wins = 0
    for t in tasks:
        a = cells.get((t, arm_a))
        b = cells.get((t, arm_b))
        if a is None or b is None or a.mean_score is None or b.mean_score is None:
            continue
        n += 1
        direction = direction_by_task[t]
        if direction == "maximize":
            if a.mean_score > b.mean_score:
                wins += 1
        else:
            if a.mean_score < b.mean_score:
                wins += 1
    return round(wins / n, 4) if n else 0.0


# ── post-injection delta per task ──────────────────────────────────────


def post_injection_deltas(
    results: Sequence[TaskRunResult],
    arm: ArmName,
) -> dict[str, float | None]:
    """``{task_id → arm.mean − baseline.mean}`` signed by direction.

    None when either side has no valid data.
    """
    cells = _aggregate_cells(results)
    direction_by_task = {r.task_id: r.objective_direction for r in results}
    tasks = sorted({r.task_id for r in results})
    out: dict[str, float | None] = {}
    for t in tasks:
        a = cells.get((t, arm))
        b = cells.get((t, "baseline"))
        if a is None or b is None or a.mean_score is None or b.mean_score is None:
            out[t] = None
            continue
        if direction_by_task[t] == "maximize":
            out[t] = round(a.mean_score - b.mean_score, 6)
        else:
            out[t] = round(b.mean_score - a.mean_score, 6)
    return out


# ── significance: paired sign-test approximation ───────────────────────


def paired_trend_p_value(
    results: Sequence[TaskRunResult],
    arm: ArmName,
    *,
    against: ArmName = "baseline",
) -> dict[str, float | None]:
    """Per-task two-sample t-style p-value for arm vs ``against``.

    Pure-python implementation — no scipy dependency.  Uses Welch's t
    statistic and a normal approximation for the p-value, which is
    fine at small n (the plan calls for "statistically significant
    trend" at p < 0.1, not a strict α=0.05 cut).

    Returns ``{task_id → p_value}``; ``None`` when either arm has
    fewer than 2 valid seeds.
    """
    by_task_arm: dict[tuple[str, ArmName], list[float]] = defaultdict(list)
    direction_by_task = {r.task_id: r.objective_direction for r in results}
    for r in results:
        if r.valid and r.score is not None:
            by_task_arm[(r.task_id, r.arm)].append(r.score)
    tasks = sorted({r.task_id for r in results})

    out: dict[str, float | None] = {}
    for t in tasks:
        xs = by_task_arm.get((t, arm), [])
        ys = by_task_arm.get((t, against), [])
        if len(xs) < 2 or len(ys) < 2:
            out[t] = None
            continue
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        vx, vy = statistics.variance(xs), statistics.variance(ys)
        if vx == 0 and vy == 0:
            # Both arms are deterministic. Same mean → no evidence of a trend.
            # Different mean with zero variance is treated as a perfect separation.
            out[t] = 1.0 if mx == my else 0.0
            continue
        denom = math.sqrt(vx / len(xs) + vy / len(ys))
        if denom == 0:
            out[t] = 1.0
            continue
        # Direction-aware: for "minimize", flip sign so positive t
        # means "arm better than baseline" in both regimes.
        sign = 1.0 if direction_by_task[t] == "maximize" else -1.0
        t_stat = sign * (mx - my) / denom
        # Normal-approx two-sided p — fine for the trend gate.
        p = 2 * (1 - _normal_cdf(abs(t_stat)))
        out[t] = round(p, 4)
    return out


def _normal_cdf(z: float) -> float:
    """Standard normal CDF via erf — pure stdlib."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ── performance profile (Frontier-Eng style) ────────────────────────────


def performance_profile(
    results: Sequence[TaskRunResult],
    arms: Sequence[ArmName] = ALL_ARMS,
    *,
    taus: Sequence[float] = (1.0, 1.05, 1.1, 1.25, 1.5, 2.0, 5.0, 10.0),
) -> dict[ArmName, dict[float, float]]:
    """Performance-profile curves per Frontier-Eng's official rubric.

    For each task, compute the best mean score across the arms.  An
    arm "solves" the task at tolerance τ ≥ 1 if its score is within τ
    of the best (≥ best/τ for maximize, ≤ best·τ for minimize).
    Returns ``{arm → {τ → fraction_of_tasks_solved}}``.

    Profile-curve area is the natural single-number summary
    downstream callers can integrate over.
    """
    cells = _aggregate_cells(results)
    tasks = sorted({r.task_id for r in results})
    direction_by_task = {r.task_id: r.objective_direction for r in results}

    # For each task, find the best score across the input arms.
    best_per_task: dict[str, tuple[float, ObjectiveDirection]] = {}
    for t in tasks:
        valid_scores: list[float] = []
        for a in arms:
            cell = cells.get((t, a))
            if cell is not None and cell.mean_score is not None:
                valid_scores.append(cell.mean_score)
        if not valid_scores:
            continue
        direction = direction_by_task[t]
        best = max(valid_scores) if direction == "maximize" else min(valid_scores)
        best_per_task[t] = (best, direction)

    out: dict[ArmName, dict[float, float]] = {}
    for arm in arms:
        per_tau: dict[float, float] = {}
        for tau in taus:
            n_solved = 0
            n_total = 0
            for t, (best, direction) in best_per_task.items():
                cell = cells.get((t, arm))
                if cell is None or cell.mean_score is None:
                    continue
                n_total += 1
                score = cell.mean_score
                if direction == "maximize":
                    threshold = best / tau if best > 0 else best * tau
                    if score >= threshold:
                        n_solved += 1
                else:
                    threshold = best * tau if best > 0 else best / tau
                    if score <= threshold:
                        n_solved += 1
            per_tau[tau] = round(n_solved / n_total, 4) if n_total else 0.0
        out[arm] = per_tau
    return out


# ── plan §Sprint 8 acceptance gates ─────────────────────────────────────


@dataclass(frozen=True)
class AcceptanceGate:
    name: str
    description: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class AcceptanceReport:
    gates: list[AcceptanceGate] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(g.passed for g in self.gates)

    @property
    def n_passed(self) -> int:
        return sum(1 for g in self.gates if g.passed)


def acceptance_report(
    results: Sequence[TaskRunResult],
    *,
    arms: Sequence[ArmName] = ALL_ARMS,
    sig_threshold: float = 0.1,
    min_positive_delta_tasks: int = 6,
    min_significant_tasks: int = 4,
) -> AcceptanceReport:
    """Plan §Sprint 8 acceptance:

    1. True_Know > Placebo_Know (lower avg_rank)
    2. True_Know > Shuffled_Know (lower avg_rank)
    3. TaskPack + CATALYST > Baseline (lower avg_rank)
    4. ≥ ``min_positive_delta_tasks`` of N have positive
       post-injection delta for True_Know vs Baseline
    5. ≥ ``min_significant_tasks`` of N reach ``p < sig_threshold`` for
       True_Know vs Baseline (paired trend approximation)
    """
    summaries = compute_arm_summaries(results, arms=arms)
    deltas = post_injection_deltas(results, "true_know")
    pvals = paired_trend_p_value(results, "true_know")

    gates: list[AcceptanceGate] = []

    tk = summaries.get("true_know")
    pk = summaries.get("placebo_know")
    sk = summaries.get("shuffled_know")
    base = summaries.get("baseline")
    pack_cat = summaries.get("task_pack_plus_catalyst")

    gates.append(
        AcceptanceGate(
            name="true_know_beats_placebo",
            description="True_Know.avg_rank < Placebo_Know.avg_rank",
            passed=bool(tk and pk and tk.avg_rank < pk.avg_rank),
            detail=(
                f"true_know.avg_rank={tk.avg_rank if tk else 'n/a'} "
                f"vs placebo_know.avg_rank={pk.avg_rank if pk else 'n/a'}"
            ),
        )
    )

    gates.append(
        AcceptanceGate(
            name="true_know_beats_shuffled",
            description="True_Know.avg_rank < Shuffled_Know.avg_rank",
            passed=bool(tk and sk and tk.avg_rank < sk.avg_rank),
            detail=(
                f"true_know.avg_rank={tk.avg_rank if tk else 'n/a'} "
                f"vs shuffled_know.avg_rank={sk.avg_rank if sk else 'n/a'}"
            ),
        )
    )

    gates.append(
        AcceptanceGate(
            name="pack_plus_catalyst_beats_baseline",
            description="TaskPack+CATALYST.avg_rank < Baseline.avg_rank",
            passed=bool(pack_cat and base and pack_cat.avg_rank < base.avg_rank),
            detail=(
                f"pack+catalyst.avg_rank={pack_cat.avg_rank if pack_cat else 'n/a'} "
                f"vs baseline.avg_rank={base.avg_rank if base else 'n/a'}"
            ),
        )
    )

    n_positive = sum(1 for d in deltas.values() if d is not None and d > 0)
    n_total_with_data = sum(1 for d in deltas.values() if d is not None)
    gates.append(
        AcceptanceGate(
            name="positive_delta_majority",
            description=(
                f"≥{min_positive_delta_tasks}/{n_total_with_data} tasks "
                f"have positive True_Know vs Baseline delta"
            ),
            passed=n_positive >= min_positive_delta_tasks,
            detail=f"{n_positive}/{n_total_with_data} positive",
        )
    )

    significant_tasks = [
        t
        for t, p in pvals.items()
        if p is not None and p < sig_threshold and (deltas.get(t) or 0) > 0
    ]
    gates.append(
        AcceptanceGate(
            name="significant_trend_count",
            description=(
                f"≥{min_significant_tasks}/{n_total_with_data} tasks "
                f"reach p<{sig_threshold} with positive delta"
            ),
            passed=len(significant_tasks) >= min_significant_tasks,
            detail=(f"significant tasks: {significant_tasks}" if significant_tasks else "none"),
        )
    )

    return AcceptanceReport(gates=gates)


# ── report serialisation ────────────────────────────────────────────────


def render_markdown(
    results: Sequence[TaskRunResult],
    *,
    arms: Sequence[ArmName] = ALL_ARMS,
) -> str:
    """Compact markdown summary for stdout / CI artifacts."""
    summaries = compute_arm_summaries(results, arms=arms)
    accept = acceptance_report(results, arms=arms)
    n_tasks = len({r.task_id for r in results})
    n_seeds = len({r.seed for r in results})

    body: list[str] = []
    body.append("# Sprint 8 — 6-arm A/B harness summary")
    body.append("")
    body.append(
        f"**Tasks**: {n_tasks}    **Arms**: {len(arms)}    "
        f"**Seeds**: {n_seeds}    **Trials**: {len(results)}"
    )
    body.append("")
    body.append("## Per-arm aggregates")
    body.append("")
    body.append(
        "| arm | avg_rank ↓ | win_vs_baseline | Δ_post_injection | validity | mean_hint_use |"
    )
    body.append("|---|---:|---:|---:|---:|---:|")
    for a in arms:
        s = summaries.get(a)
        if s is None:
            continue
        body.append(
            f"| `{s.arm}` | {s.avg_rank:.3f} | "
            f"{s.win_rate_vs_baseline:.0%} | "
            f"{s.avg_post_injection_delta_vs_baseline:+.4f} | "
            f"{s.validity_preservation_rate:.0%} | "
            f"{s.mean_hint_use_rate:.0%} |"
        )
    body.append("")
    body.append("## Acceptance gates")
    body.append("")
    for g in accept.gates:
        mark = "✅" if g.passed else "❌"
        body.append(f"- {mark} **{g.name}** — {g.description}.  {g.detail}")
    body.append("")
    body.append(f"**Verdict**: {accept.n_passed}/{len(accept.gates)} gates passed")
    body.append("")
    return "\n".join(body)


def to_jsonable(
    results: Sequence[TaskRunResult],
    *,
    arms: Sequence[ArmName] = ALL_ARMS,
) -> dict:
    """JSON-serialisable dump for offline storage."""
    summaries = compute_arm_summaries(results, arms=arms)
    accept = acceptance_report(results, arms=arms)
    profile = performance_profile(results, arms=arms)
    return {
        "schema_version": "1.0",
        "n_trials": len(results),
        "tasks": sorted({r.task_id for r in results}),
        "arms": list(arms),
        "seeds": sorted({r.seed for r in results}),
        "arm_summaries": {a: asdict(s) for a, s in summaries.items()},
        "performance_profile": {
            a: {str(tau): v for tau, v in tau_map.items()} for a, tau_map in profile.items()
        },
        "post_injection_deltas_true_know": post_injection_deltas(results, "true_know"),
        "paired_trend_p_values_true_know": paired_trend_p_value(results, "true_know"),
        "acceptance": {
            "n_passed": accept.n_passed,
            "n_total": len(accept.gates),
            "all_passed": accept.all_passed,
            "gates": [asdict(g) for g in accept.gates],
        },
    }


__all__ = [
    "ALL_ARMS",
    "AcceptanceGate",
    "AcceptanceReport",
    "ArmName",
    "ArmSummary",
    "ObjectiveDirection",
    "RunFn",
    "TaskArmSummary",
    "TaskRunResult",
    "TaskSpec",
    "acceptance_report",
    "compute_arm_summaries",
    "pairwise_win_rate",
    "paired_trend_p_value",
    "performance_profile",
    "post_injection_deltas",
    "render_markdown",
    "run_matrix",
    "to_jsonable",
]
