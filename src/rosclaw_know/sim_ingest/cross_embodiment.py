"""Sprint 9 + 10: cross-embodiment pattern reuse harness.

Plan §Sprint 9 acceptance gate:

> 同一 pattern 能在两个 embodiment 上复用 — 例如 anti-windup
> 同时用于 quadrotor PID 和 arm joint PID

Sprint 9 first shipped a *hand-curated* ``PATTERN_TRANSFER_TABLE`` to
implement that gate.  Sprint 10 replaces the table with a pure function
that mines the mapping from the FailureMode catalog + FixPattern.failure_ids
— no human-authored ``event_type → pattern_id`` rows.

Given a list of :class:`MappedFailure` (produced by :mod:`event_to_failure`),
:func:`run_cross_embodiment_check` reports:

1. **Failure-level reuse** — :class:`FailureMode` instances seen on ≥2
   embodiments (direct dedup proof from :class:`EventToFailureMapper`).
2. **Pattern-level reuse** — canonical fix patterns transferable to
   multiple embodiments, computed from the auto-derived transfer table
   (or an explicit ``transfer_table=`` injection for tests / what-if).

The harness is *static* — it doesn't re-run agents — so it's cheap
enough to run on every CI commit, and the report is deterministic given
the same MappedFailure inputs.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..schemas import FailureMode, FixPattern
from .event_schema import EVENT_TYPES
from .event_to_failure import MappedFailure

logger = logging.getLogger(__name__)

# ── Vocabulary: RobotEvent event_type → catalog tokens ──────────────────
#
# Sprint 10 replaces the hand-curated ``PATTERN_TRANSFER_TABLE`` with a
# structural join.  The join needs *one* small piece of vocabulary: how
# does a RobotEvent ``event_type`` (rosbag/Isaac/MuJoCo signal-stream
# noun) map to catalog FailureMode domain language (the v2
# failure_taxonomy authors' words)?  Each alias below is a token that,
# when found in a FailureMode ``id``, ``normalized_symptom``,
# ``likely_causes``, or ``observable_signals``, indicates that failure
# is in this event_type's family.
#
# This is *vocabulary* — not a pattern lookup.  All ``pattern_id``s
# emitted by :func:`derive_pattern_transfer_table` come from the
# supplied :class:`FixPattern` collection's ``id`` field; this constant
# is intentionally pattern-free.

_EVENT_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "collision": (
        "collision", "contact", "contacts", "impact",
    ),
    "safety_stop": (
        "safety", "estop", "e_stop", "halt",
    ),
    "joint_limit_violation": (
        "joint", "joints", "position_limit", "torque_limit",
    ),
    "controller_error": (
        "controller", "divergence", "windup", "wind_up",
        "integral", "integrator", "pid", "tracking",
        "nan",
    ),
    "sensor_outlier": (
        "sensor", "outlier", "dropout", "spike",
    ),
    "task_timeout": (
        "timeout", "budget", "runaway", "wallclock", "wall_clock",
    ),
    "trajectory_deviation": (
        "trajectory", "follow", "deviation", "planning",
        "plan", "diverges", "diverge",
    ),
    "actuator_saturation": (
        "actuator", "saturation", "saturate", "saturated",
        "clamp", "command",
    ),
}


_WORD_RE = re.compile(r"[a-z0-9]+")


def _haystack_for(failure: FailureMode) -> set[str]:
    """Tokenize a FailureMode for keyword matching.

    Pulls tokens from the *structural identity* of the failure only:

      * ``id`` (e.g. ``failure_pid_integrator_windup``)
      * ``normalized_symptom`` (e.g. ``actuator_saturation_with_unbounded_integral``)
      * ``name``

    Deliberately ignores ``observable_signals`` and ``likely_causes`` —
    those fields contain incidental words like ``"saturation"`` inside
    ``"arithmetic-unit saturation"`` (an AES failure) that would
    over-match ``actuator_saturation``.  The structural identity fields
    pin the failure's domain language precisely.
    """
    pieces = [
        failure.id.lower(),
        failure.normalized_symptom.lower(),
        failure.name.lower(),
    ]
    return set(_WORD_RE.findall(" ".join(pieces)))


def _failure_event_types(failure: FailureMode) -> set[str]:
    """Which RobotEvent event_types does this catalog failure correspond to?

    Returns the set of event_types whose alias vocabulary overlaps with
    the failure's haystack.  Empty when the failure is purely
    software/abstract (e.g. ``failure_kv_cache_unbounded_growth``).
    """
    hay = _haystack_for(failure)
    matches: set[str] = set()
    for et in EVENT_TYPES:
        aliases = _EVENT_TYPE_ALIASES.get(et, (et, *et.split("_")))
        if hay.intersection(aliases):
            matches.add(et)
    return matches


def derive_pattern_transfer_table(
    failures: Sequence[FailureMode],
    fix_patterns: Sequence[FixPattern],
) -> dict[str, tuple[str, ...]]:
    """Mine ``event_type → tuple[pattern_id, ...]`` from the catalog.

    The mapping is the relational join

        event_type ─ Sprint 9 _EVENT_TO_FAILURE stems / event_type tokens
                   └── FailureMode (catalog) on token overlap
                       └── FixPattern.failure_ids (catalog) on equality

    No hand-curated ``event_type → pattern_id`` rows live anywhere in
    the codebase; this function and its inputs are the complete source
    of truth.

    Parameters
    ----------
    failures
        FailureMode entries from ``data/assets/failure_taxonomy.yaml``
        (or any test fixture).  ``normalized_symptom`` and ``id`` are
        what determine which event_types this failure surfaces under.
    fix_patterns
        FixPattern entries from the compiled graph
        (``data/assets/physical_graph.json``) — each one's
        ``failure_ids`` list pins which FailureMode(s) it addresses.

    Returns
    -------
    dict[event_type, tuple[pattern_id, ...]]
        Sorted tuple per event_type so the output is deterministic.
        Empty dict when no fix_pattern overlaps the catalog.
    """
    # event_type → set of catalog failure_ids
    failure_index: dict[str, set[str]] = {}
    for f in failures:
        for et in _failure_event_types(f):
            failure_index.setdefault(et, set()).add(f.id)

    # event_type → set of fix pattern ids
    transfer: dict[str, set[str]] = {}
    for fp in fix_patterns:
        for et, fids in failure_index.items():
            if any(fid in fids for fid in fp.failure_ids):
                transfer.setdefault(et, set()).add(fp.id)

    return {et: tuple(sorted(pids)) for et, pids in transfer.items()}


# ── Default loader (reads compiled graph) ───────────────────────────────


@lru_cache(maxsize=1)
def load_default_transfer_table() -> dict[str, tuple[str, ...]]:
    """Load + derive the transfer table from the canonical asset bundle.

    Reads ``physical_graph.json`` (post-build) and
    ``failure_taxonomy.yaml``.  Returns an empty dict when either is
    missing so CI / fresh checkouts don't crash.
    """
    from .. import config as _config

    assets_dir = Path(_config.ASSETS_DIR)
    graph_path = assets_dir / "physical_graph.json"
    fail_path = assets_dir / "failure_taxonomy.yaml"
    if not graph_path.is_file() or not fail_path.is_file():
        logger.warning(
            "cross_embodiment: assets missing in %s — auto transfer table empty",
            assets_dir,
        )
        return {}

    import yaml as _yaml

    fail_raw = _yaml.safe_load(fail_path.read_text(encoding="utf-8")) or {}
    failures = [FailureMode.model_validate(f) for f in fail_raw.get("failures", [])]

    graph_raw = json.loads(graph_path.read_text(encoding="utf-8"))
    fix_patterns: list[FixPattern] = []
    for n in graph_raw.get("nodes", []):
        if n.get("node_type") != "FixPattern":
            continue
        payload = n.get("payload")
        if isinstance(payload, dict):
            try:
                fix_patterns.append(FixPattern.model_validate(payload))
            except Exception:  # noqa: BLE001 — defensive against schema drift
                logger.debug("skipping malformed FixPattern node %s", n.get("id"))
                continue

    return derive_pattern_transfer_table(failures, fix_patterns)


def _invalidate_default_transfer_table_cache() -> None:
    """Test hook: drop the lru_cache so reassigned config.ASSETS_DIR is honoured."""
    load_default_transfer_table.cache_clear()


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
        """Plan §Sprint 9 gate: ≥1 pattern transferable across ≥2 embodiments."""
        return len(self.patterns_seen_on_multiple_embodiments) >= 1

    @property
    def acceptance_failure_reuse_passed(self) -> bool:
        """Stricter sibling gate: ≥1 FailureMode surfaces on ≥2 embodiments."""
        return len(self.failures_seen_on_multiple_embodiments) >= 1


# ── Harness ─────────────────────────────────────────────────────────────


def run_cross_embodiment_check(
    failures: Sequence[MappedFailure],
    *,
    transfer_table: Mapping[str, Iterable[str]] | None = None,
    known_pattern_ids: Iterable[str] | None = None,
) -> CrossEmbodimentReport:
    """Compute the cross-embodiment reuse report.

    Parameters
    ----------
    failures
        Output of :func:`event_to_failure.map_events_to_failures` —
        typically the union of every adapter's failures for a given run.
    transfer_table
        Optional ``event_type → patterns`` mapping.  When ``None`` (the
        default), the table is auto-derived from the catalog via
        :func:`load_default_transfer_table`.  Tests and what-if
        scenarios can inject a custom table here.
    known_pattern_ids
        Optional set of pattern ids that exist in the current manifest.
        When provided, transfer-table entries that name an unknown
        pattern are filtered out (with a note recorded in
        :attr:`CrossEmbodimentReport.notes`).
    """
    if transfer_table is None:
        transfer_table = load_default_transfer_table()
    table_for_lookup: dict[str, tuple[str, ...]] = {
        et: tuple(pids) for et, pids in transfer_table.items()
    }
    known = set(known_pattern_ids) if known_pattern_ids is not None else None

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
        pids = table_for_lookup.get(event_type, ())
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
        for et, pids in table_for_lookup.items():
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
    "PatternReuseRow",
    "CrossEmbodimentReport",
    "derive_pattern_transfer_table",
    "load_default_transfer_table",
    "run_cross_embodiment_check",
    "render_markdown",
)
