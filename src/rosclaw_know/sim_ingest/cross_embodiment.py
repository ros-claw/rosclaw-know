"""Sprint 9: cross-embodiment pattern reuse harness.

Plan §Sprint 9 acceptance gate:

> 同一 pattern 能在两个 embodiment 上复用 — 例如 anti-windup
> 同时用于 quadrotor PID 和 arm joint PID

This module turns that gate into a runnable check.  Given a list of
:class:`MappedFailure` (produced by :mod:`event_to_failure`), we report:

1. **Failure-level reuse**: how many distinct :class:`FailureMode`
   instances were seen on ≥2 embodiments.  Because
   :class:`EventToFailureMapper` deduplicates by ``(event_type,
   fingerprint)`` *across* embodiments, a single MappedFailure with
   ``len(embodiments_seen) ≥ 2`` is direct proof.
2. **Pattern-level reuse**: which canonical fix patterns are
   transferable to multiple embodiments, computed from a curated
   :data:`PATTERN_TRANSFER_TABLE` that captures the v1.5 plan's
   examples ("anti-windup ↔ controller_error", "vectorize_inner_loop ↔
   nothing-physical", etc.).

The harness is *static* — it doesn't re-run agents — so it's cheap
enough to run on every CI commit, and the acceptance report is
deterministic given the same MappedFailure inputs.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .event_to_failure import MappedFailure

# ── Curated transfer table ──────────────────────────────────────────────
#
# Each row says: "if FailureMode.event_type == <key>, then *these*
# pattern_ids are known to be applicable".  Authored by hand against
# the v1.5 manifest; future Sprint 9+ work can replace this with a
# similarity-scored lookup against PatternCardV2.symptom.
#
# Keep the codomain SMALL — these are pattern ids that exist in the
# current v2 manifest.  The harness doesn't validate that, but the
# accompanying tests do.

PATTERN_TRANSFER_TABLE: Mapping[str, tuple[str, ...]] = {
    "controller_error": (
        "anti_windup",
        "controller_output_clamp",
        "add_boundary_validation",
    ),
    "actuator_saturation": (
        "anti_windup",
        "controller_output_clamp",
    ),
    "joint_limit_violation": (
        "controller_output_clamp",
        "add_boundary_validation",
    ),
    "trajectory_deviation": (
        "warm_start_from_prior_best",
        "controller_output_clamp",
    ),
    "task_timeout": (
        "add_time_budget",
        "generic_time_budget",
    ),
    "sensor_outlier": (
        "add_boundary_validation",
    ),
    "collision": (
        "add_boundary_validation",
    ),
    "safety_stop": (
        "add_boundary_validation",
    ),
}


# ── Report types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PatternReuseRow:
    """One row of the cross-embodiment report."""

    pattern_id: str
    failure_event_types: tuple[str, ...]
    embodiments: tuple[str, ...]

    @property
    def is_cross_embodiment(self) -> bool:
        return len(self.embodiments) >= 2


@dataclass(frozen=True)
class CrossEmbodimentReport:
    """Result of running the Sprint 9 acceptance harness."""

    failures_seen_on_multiple_embodiments: tuple[MappedFailure, ...]
    patterns_seen_on_multiple_embodiments: tuple[PatternReuseRow, ...]
    all_pattern_rows: tuple[PatternReuseRow, ...]
    distinct_embodiments: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def acceptance_pattern_reuse_passed(self) -> bool:
        """The plan §Sprint 9 gate: ≥1 pattern survives on ≥2 embodiments."""
        return len(self.patterns_seen_on_multiple_embodiments) >= 1

    @property
    def acceptance_failure_reuse_passed(self) -> bool:
        """Stricter sibling gate: ≥1 FailureMode surfaces on ≥2 embodiments."""
        return len(self.failures_seen_on_multiple_embodiments) >= 1


# ── Harness ─────────────────────────────────────────────────────────────


def _pattern_ids_for(event_type: str) -> tuple[str, ...]:
    return PATTERN_TRANSFER_TABLE.get(event_type, ())


def run_cross_embodiment_check(
    failures: Sequence[MappedFailure],
    *,
    known_pattern_ids: Iterable[str] | None = None,
) -> CrossEmbodimentReport:
    """Compute the cross-embodiment reuse report.

    Parameters
    ----------
    failures
        Output of :func:`event_to_failure.map_events_to_failures` —
        typically the union of every adapter's failures for a given
        run.
    known_pattern_ids
        Optional set of pattern ids that exist in the current
        manifest.  When provided, transfer-table entries that name an
        unknown pattern are filtered out (with a note in
        :attr:`CrossEmbodimentReport.notes`).
    """
    known = set(known_pattern_ids) if known_pattern_ids is not None else None

    # pattern_id → {event_type: ..., embodiments: {...}}
    pattern_rows: dict[str, dict] = {}
    failures_multi: list[MappedFailure] = []
    distinct_embodiments: set[str] = set()
    notes: list[str] = []

    for mf in failures:
        for emb in mf.embodiments_seen:
            distinct_embodiments.add(emb)
        if len(mf.embodiments_seen) >= 2:
            failures_multi.append(mf)
        event_type = mf.failure.normalized_symptom.split("::", 1)[0]
        pids = _pattern_ids_for(event_type)
        for pid in pids:
            if known is not None and pid not in known:
                continue
            row = pattern_rows.setdefault(pid, {"event_types": set(), "embodiments": set()})
            row["event_types"].add(event_type)
            for emb in mf.embodiments_seen:
                row["embodiments"].add(emb)

    if known is not None:
        # Surface any transfer-table pattern that *would* have applied
        # but doesn't exist in the manifest.  Helps catch manifest drift.
        for et, pids in PATTERN_TRANSFER_TABLE.items():
            for pid in pids:
                if pid not in known and any(
                    mf.failure.normalized_symptom.startswith(et + "::") for mf in failures
                ):
                    notes.append(
                        f"pattern '{pid}' for event_type '{et}' not in current manifest"
                    )

    all_rows: list[PatternReuseRow] = []
    for pid, row in sorted(pattern_rows.items()):
        all_rows.append(PatternReuseRow(
            pattern_id=pid,
            failure_event_types=tuple(sorted(row["event_types"])),
            embodiments=tuple(sorted(row["embodiments"])),
        ))

    multi = tuple(r for r in all_rows if r.is_cross_embodiment)

    return CrossEmbodimentReport(
        failures_seen_on_multiple_embodiments=tuple(failures_multi),
        patterns_seen_on_multiple_embodiments=multi,
        all_pattern_rows=tuple(all_rows),
        distinct_embodiments=tuple(sorted(distinct_embodiments)),
        notes=tuple(notes),
    )


def render_markdown(report: CrossEmbodimentReport) -> str:
    """Compact markdown table fit for stdout / CI logs."""
    lines: list[str] = []
    lines.append("# Sprint 9 — cross-embodiment reuse report")
    lines.append("")
    lines.append(f"- distinct embodiments observed: {len(report.distinct_embodiments)}")
    lines.append(
        f"- failure modes on ≥2 embodiments: "
        f"{len(report.failures_seen_on_multiple_embodiments)}"
    )
    lines.append(
        f"- patterns on ≥2 embodiments: "
        f"{len(report.patterns_seen_on_multiple_embodiments)}"
    )
    lines.append("")
    lines.append("## Pattern reuse")
    lines.append("")
    lines.append("| pattern_id | event_types | embodiments | cross-embodiment? |")
    lines.append("|---|---|---|---|")
    for row in report.all_pattern_rows:
        et = ", ".join(row.failure_event_types) or "—"
        emb = ", ".join(row.embodiments) or "—"
        mark = "✅" if row.is_cross_embodiment else " "
        lines.append(f"| {row.pattern_id} | {et} | {emb} | {mark} |")
    lines.append("")
    lines.append("## Acceptance gates")
    lines.append("")
    g1 = "✅" if report.acceptance_failure_reuse_passed else "❌"
    g2 = "✅" if report.acceptance_pattern_reuse_passed else "❌"
    lines.append(f"- {g1}  ≥1 FailureMode seen on ≥2 embodiments")
    lines.append(f"- {g2}  ≥1 pattern transferable across ≥2 embodiments")
    if report.notes:
        lines.append("")
        lines.append("## Notes")
        for n in report.notes:
            lines.append(f"- {n}")
    return "\n".join(lines)


__all__ = (
    "PATTERN_TRANSFER_TABLE",
    "PatternReuseRow",
    "CrossEmbodimentReport",
    "run_cross_embodiment_check",
    "render_markdown",
)
