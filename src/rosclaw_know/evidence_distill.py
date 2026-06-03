"""Sprint 6: Evidence Loop V2 — distil EvidenceTrace JSONL into per-pattern,
per-arm statistics (plan §11.8).

Adds *causal* evidence on top of the v1 ``feedback_distill`` (which only
computed raw uplift across all outcomes).  Sprint 6 separates the four
experimental arms — ``baseline / true / placebo / shuffled`` — so we can
report the *placebo-adjusted* uplift the plan §11.8 acceptance demands::

    placebo_adjusted_uplift  = mean(true.best_delta_5) − mean(placebo.best_delta_5)
    shuffled_adjusted_uplift = mean(true.best_delta_5) − mean(shuffled.best_delta_5)

Promotion / demotion downstream (see :mod:`bridge_reweighter`) prefers
the adjusted value: a pattern that wins against placebo proves it really
did help, not just that the agent got lucky on this task.

Metrics produced per pattern
-----------------------------

* ``n`` total observations
* ``n_by_arm`` how many for each of the four arms
* ``avg_uplift_1 / 3 / 5`` mean of ``best_delta_5`` (over true arm)
* ``win_rate`` fraction with ``best_delta_5 > WIN_DELTA_THRESHOLD``
* ``hint_use_rate`` fraction with ``used_hint == True`` (true arm only)
* ``validity_preservation_rate`` fraction with verifier_status==valid
* ``regression_rate`` fraction with ``best_delta_5 < 0``
* ``placebo_adjusted_uplift`` true mean − placebo mean (None if either arm empty)
* ``shuffled_adjusted_uplift`` true mean − shuffled mean
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import ASSETS_DIR
from .schemas import EvidenceTrace

log = logging.getLogger("rosclaw_know.evidence_distill")

# ── thresholds ──────────────────────────────────────────────────────────

WIN_DELTA_THRESHOLD = 0.05
"""Same magnitude the v1 distiller used — keep parity for callers that
read both metrics during the migration window."""

MIN_SAMPLE_SIZE = 5
"""Lower bound for any promotion / demotion decision."""

ADJUSTED_PROMOTE_THRESHOLD = 0.03
"""Plan §Sprint 6: promote only when placebo_adjusted_uplift exceeds this."""

ADJUSTED_DEMOTE_THRESHOLD = -0.03
"""Plan §Sprint 6: demote when placebo_adjusted_uplift is *below* this."""


Arm = Literal["baseline", "true", "placebo", "shuffled"]
ALL_ARMS: tuple[Arm, ...] = ("baseline", "true", "placebo", "shuffled")


# ── per-arm stats ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArmStats:
    """Aggregated statistics for one experimental arm of one pattern."""

    arm: str
    n: int
    avg_uplift_1: float | None
    avg_uplift_3: float | None
    avg_uplift_5: float | None
    win_rate: float
    regression_rate: float
    validity_preservation_rate: float


@dataclass(frozen=True)
class EvidenceStat:
    """Aggregated statistics for one pattern across all arms."""

    pattern_id: str
    n: int
    n_by_arm: dict[str, int]
    by_arm: dict[str, ArmStats]
    hint_use_rate: float
    placebo_adjusted_uplift: float | None
    shuffled_adjusted_uplift: float | None
    raw_uplift_mean: float
    """Mean ``best_delta_5`` across all arms (true + others) — kept for
    backwards compat with v1 callers that index on ``uplift_mean``."""
    last_seen: str


# ── coverage diagnostics (plan §Sprint 6 acceptance gates) ──────────────


@dataclass(frozen=True)
class CoverageReport:
    """Plan §Sprint 6 acceptance: gates on the JSONL stream as a whole."""

    total: int
    catalyst_total: int
    """How many CATALYST-strategy traces we saw — denominator for the
    injection_id and post_score gates below."""

    catalyst_with_injection_id: int
    catalyst_with_post_score_3: int
    catalyst_with_post_score_5: int
    catalyst_with_code_diff_summary: int

    violations: list[str] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────────────────


def _safe_mean(values: Sequence[float]) -> float | None:
    """statistics.fmean that returns None on empty sequence."""
    return statistics.fmean(values) if values else None


def _round_or_none(v: float | None, ndigits: int = 4) -> float | None:
    return round(v, ndigits) if v is not None else None


def _arm_stats(arm: Arm, traces: Sequence[EvidenceTrace]) -> ArmStats:
    """Aggregate one arm's traces."""
    n = len(traces)
    if n == 0:
        return ArmStats(
            arm=arm, n=0, avg_uplift_1=None, avg_uplift_3=None,
            avg_uplift_5=None, win_rate=0.0, regression_rate=0.0,
            validity_preservation_rate=0.0,
        )
    deltas_1 = [t.post_score_1 - t.pre_score for t in traces if t.post_score_1 is not None]
    deltas_3 = [t.post_score_3 - t.pre_score for t in traces if t.post_score_3 is not None]
    deltas_5 = [t.best_delta_5 for t in traces if t.best_delta_5 is not None]
    wins = sum(1 for d in deltas_5 if d > WIN_DELTA_THRESHOLD)
    regressions = sum(1 for d in deltas_5 if d < 0)
    valids = sum(1 for t in traces if t.verifier_status == "valid")
    denom = max(len(deltas_5), 1)
    return ArmStats(
        arm=arm,
        n=n,
        avg_uplift_1=_round_or_none(_safe_mean(deltas_1)),
        avg_uplift_3=_round_or_none(_safe_mean(deltas_3)),
        avg_uplift_5=_round_or_none(_safe_mean(deltas_5)),
        win_rate=round(wins / denom, 4),
        regression_rate=round(regressions / denom, 4),
        validity_preservation_rate=round(valids / n, 4),
    )


def _adjusted_uplift(
    true: ArmStats, control: ArmStats
) -> float | None:
    """``mean(true.best_delta_5) - mean(control.best_delta_5)``.

    Returns None when either arm has no best_delta_5 samples — refusing
    to report a misleading "treatment - 0" when the control is empty.
    """
    if true.avg_uplift_5 is None or control.avg_uplift_5 is None:
        return None
    return round(true.avg_uplift_5 - control.avg_uplift_5, 4)


# ── main entry ──────────────────────────────────────────────────────────


def distill(
    traces: Iterable[EvidenceTrace],
) -> tuple[dict[str, EvidenceStat], CoverageReport]:
    """Aggregate traces into per-pattern stats + a coverage report.

    Traces without a ``pattern_id`` are still counted in the coverage
    report but contribute no per-pattern statistic.
    """
    # Bucket by pattern then by arm.
    buckets: dict[str, dict[Arm, list[EvidenceTrace]]] = defaultdict(
        lambda: {a: [] for a in ALL_ARMS}
    )
    total = 0
    catalyst_total = 0
    catalyst_with_inj = 0
    catalyst_with_p3 = 0
    catalyst_with_p5 = 0
    catalyst_with_diff = 0
    for t in traces:
        total += 1
        if t.strategy == "CATALYST":
            catalyst_total += 1
            if t.injection_id is not None:
                catalyst_with_inj += 1
            if t.post_score_3 is not None:
                catalyst_with_p3 += 1
            if t.post_score_5 is not None:
                catalyst_with_p5 += 1
            if t.code_diff_summary:
                catalyst_with_diff += 1
        if t.pattern_id is None:
            continue
        buckets[t.pattern_id][t.arm].append(t)

    # Compute per-pattern stats.
    out: dict[str, EvidenceStat] = {}
    for pid, by_arm_traces in buckets.items():
        n = sum(len(ts) for ts in by_arm_traces.values())
        by_arm = {a: _arm_stats(a, by_arm_traces[a]) for a in ALL_ARMS}
        # Hint-use only meaningful on the true arm (placebo/shuffled by
        # design don't carry a real hint).
        true_arm = by_arm_traces["true"]
        hint_n = len(true_arm)
        hint_used = sum(1 for t in true_arm if t.used_hint)
        hint_use_rate = round(hint_used / hint_n, 4) if hint_n > 0 else 0.0

        adj_placebo = _adjusted_uplift(by_arm["true"], by_arm["placebo"])
        adj_shuffled = _adjusted_uplift(by_arm["true"], by_arm["shuffled"])

        # Raw uplift across all arms — backwards-compat hook.
        all_deltas = [
            t.best_delta_5
            for ts in by_arm_traces.values()
            for t in ts
            if t.best_delta_5 is not None
        ]
        raw_mean = round(statistics.fmean(all_deltas), 4) if all_deltas else 0.0

        last_seen = max(
            (t.timestamp for ts in by_arm_traces.values() for t in ts if t.timestamp),
            default="",
        )

        out[pid] = EvidenceStat(
            pattern_id=pid,
            n=n,
            n_by_arm={a: by_arm[a].n for a in ALL_ARMS},
            by_arm=by_arm,
            hint_use_rate=hint_use_rate,
            placebo_adjusted_uplift=adj_placebo,
            shuffled_adjusted_uplift=adj_shuffled,
            raw_uplift_mean=raw_mean,
            last_seen=last_seen,
        )

    # Coverage gates.
    violations: list[str] = []
    if catalyst_total > 0:
        if catalyst_with_inj < catalyst_total:
            violations.append(
                f"injection_id missing on "
                f"{catalyst_total - catalyst_with_inj}/{catalyst_total} CATALYST traces"
            )
        # Plan §Sprint 6: at least 80% of CATALYST have post_score_3+5
        p3_rate = catalyst_with_p3 / catalyst_total
        p5_rate = catalyst_with_p5 / catalyst_total
        if p3_rate < 0.8:
            violations.append(
                f"post_score_3 coverage {p3_rate:.0%} < 80% gate "
                f"({catalyst_with_p3}/{catalyst_total})"
            )
        if p5_rate < 0.8:
            violations.append(
                f"post_score_5 coverage {p5_rate:.0%} < 80% gate "
                f"({catalyst_with_p5}/{catalyst_total})"
            )
        diff_rate = catalyst_with_diff / catalyst_total
        if diff_rate < 0.5:
            violations.append(
                f"code_diff_summary coverage {diff_rate:.0%} < 50% gate "
                f"({catalyst_with_diff}/{catalyst_total})"
            )

    coverage = CoverageReport(
        total=total,
        catalyst_total=catalyst_total,
        catalyst_with_injection_id=catalyst_with_inj,
        catalyst_with_post_score_3=catalyst_with_p3,
        catalyst_with_post_score_5=catalyst_with_p5,
        catalyst_with_code_diff_summary=catalyst_with_diff,
        violations=violations,
    )
    return out, coverage


# ── promotion / demotion logic ──────────────────────────────────────────


def is_promoted(stat: EvidenceStat) -> bool:
    """Plan §Sprint 6: promote when ``placebo_adjusted_uplift`` clears
    :data:`ADJUSTED_PROMOTE_THRESHOLD` and the true arm has enough samples."""
    true_n = stat.n_by_arm.get("true", 0)
    if true_n < MIN_SAMPLE_SIZE:
        return False
    if stat.placebo_adjusted_uplift is None:
        return False
    return stat.placebo_adjusted_uplift >= ADJUSTED_PROMOTE_THRESHOLD


def is_demoted(stat: EvidenceStat) -> bool:
    """Plan §Sprint 6: demote when ``placebo_adjusted_uplift`` is below
    :data:`ADJUSTED_DEMOTE_THRESHOLD` with enough samples."""
    true_n = stat.n_by_arm.get("true", 0)
    if true_n < MIN_SAMPLE_SIZE:
        return False
    if stat.placebo_adjusted_uplift is None:
        return False
    return stat.placebo_adjusted_uplift <= ADJUSTED_DEMOTE_THRESHOLD


# ── serialisation ───────────────────────────────────────────────────────


def stat_to_dict(stat: EvidenceStat) -> dict[str, Any]:
    """JSON-serialisable form of one EvidenceStat."""
    return {
        "pattern_id": stat.pattern_id,
        "n": stat.n,
        "n_by_arm": dict(stat.n_by_arm),
        "by_arm": {a: asdict(s) for a, s in stat.by_arm.items()},
        "hint_use_rate": stat.hint_use_rate,
        "placebo_adjusted_uplift": stat.placebo_adjusted_uplift,
        "shuffled_adjusted_uplift": stat.shuffled_adjusted_uplift,
        "raw_uplift_mean": stat.raw_uplift_mean,
        "last_seen": stat.last_seen,
        "is_promoted": is_promoted(stat),
        "is_demoted": is_demoted(stat),
    }


def write_stats(
    stats: dict[str, EvidenceStat],
    coverage: CoverageReport,
    *,
    out_path: Path | None = None,
) -> Path:
    """Write ``data/assets/evidence_stats.json``.  Atomic via tmp+rename."""
    out_path = out_path or (ASSETS_DIR / "evidence_stats.json")
    payload = {
        "schema_version": "2.0",
        "win_delta_threshold": WIN_DELTA_THRESHOLD,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "adjusted_promote_threshold": ADJUSTED_PROMOTE_THRESHOLD,
        "adjusted_demote_threshold": ADJUSTED_DEMOTE_THRESHOLD,
        "coverage": {
            "total": coverage.total,
            "catalyst_total": coverage.catalyst_total,
            "catalyst_with_injection_id": coverage.catalyst_with_injection_id,
            "catalyst_with_post_score_3": coverage.catalyst_with_post_score_3,
            "catalyst_with_post_score_5": coverage.catalyst_with_post_score_5,
            "catalyst_with_code_diff_summary": coverage.catalyst_with_code_diff_summary,
            "violations": list(coverage.violations),
        },
        "patterns": {pid: stat_to_dict(s) for pid, s in sorted(stats.items())},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(out_path)
    return out_path


__all__ = [
    "ADJUSTED_DEMOTE_THRESHOLD",
    "ADJUSTED_PROMOTE_THRESHOLD",
    "ALL_ARMS",
    "ArmStats",
    "CoverageReport",
    "EvidenceStat",
    "MIN_SAMPLE_SIZE",
    "WIN_DELTA_THRESHOLD",
    "distill",
    "is_demoted",
    "is_promoted",
    "stat_to_dict",
    "write_stats",
]
