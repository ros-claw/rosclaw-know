"""Sprint 11: real-robot trajectory → CandidatePattern extractor.

When the agent uses an *unknown* pattern_id on a real robot and the
placebo-controlled evidence (Sprint 11 evidence loop) shows it
works, that's a self-improvement signal: the field has discovered a
pattern the offline catalog doesn't know about.  This module turns
those traces into :class:`schemas.CandidatePattern` entries, ready for
Sprint 4 (pattern compiler) to graduate into the v2 catalog.

The extractor is intentionally *conservative*:

  * Only patterns with ≥ ``MIN_TRACE_COUNT`` (3) traces survive — single
    one-offs are noise.
  * Only patterns with ``placebo_adjusted_uplift > PROMOTE_THRESHOLD``
    survive — patterns that don't beat placebo aren't worth the
    catalog slot.
  * Pattern_ids already in ``known_pattern_ids`` are filtered out —
    real-robot evidence for known patterns flows through Sprint 6's
    bridge_reweighter, not here.

Output ``CandidatePattern.id`` is normalised to
``candidate_real_robot_<pattern_id>`` (or
``candidate_real_robot_<sanitised_task_name>`` when the trace lacks a
pattern_id).  Each successful_mutation has ``kind="other"`` because
real-robot traces don't carry structural code diffs the Sprint 3
extractor could classify.  The ``description`` quotes the trace's
``code_diff_summary`` so downstream review has the agent's own words
on what changed.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import fmean

from ..evidence_distill import ADJUSTED_PROMOTE_THRESHOLD
from ..schemas import CandidatePattern, EvidenceTrace, Mutation

log = logging.getLogger("rosclaw_know.sim_ingest.robot_trajectory_extractor")

MIN_TRACE_COUNT = 3
"""Below this many traces per pattern, the signal is too noisy."""

PROMOTE_THRESHOLD = ADJUSTED_PROMOTE_THRESHOLD
"""Match Sprint 6's promote threshold so the two loops agree."""

_ID_SAFE_RE = re.compile(r"[^a-z0-9_]+")


def _sanitise(s: str) -> str:
    return _ID_SAFE_RE.sub("_", s.lower()).strip("_")


def _candidate_id_for(pattern_id: str | None, task_name: str | None) -> str:
    if pattern_id:
        return f"candidate_real_robot_{_sanitise(pattern_id)}"
    return f"candidate_real_robot_{_sanitise(task_name or 'unknown')}"


def _bucket_key(trace: EvidenceTrace) -> str:
    """Group key — when pattern_id is missing, fall back to task_name."""
    return trace.pattern_id or f"__task__:{trace.task_name}"


def _placebo_adjusted_uplift(true_deltas: Sequence[float],
                             placebo_deltas: Sequence[float]) -> float | None:
    """Match :func:`evidence_distill._adjusted_uplift` signal."""
    if not true_deltas or not placebo_deltas:
        return None
    return round(fmean(true_deltas) - fmean(placebo_deltas), 4)


def _task_family_for(task_name: str) -> str:
    """Pick a stable task_family for the candidate.

    Today we don't have a robust mapping from real-robot task_name to
    Frontier-Eng family.  Use ``robotics_optimization`` — that's the
    natural fit for any task running on UR5 / quadrotor / etc.
    """
    return "robotics_optimization"


def extract_candidates_from_evidence_traces(
    traces: Iterable[EvidenceTrace],
    *,
    known_pattern_ids: Iterable[str] = (),
    min_trace_count: int = MIN_TRACE_COUNT,
    promote_threshold: float = PROMOTE_THRESHOLD,
) -> list[CandidatePattern]:
    """Mine NEW candidate patterns from real-robot evidence traces.

    Parameters
    ----------
    traces
        EvidenceTraces from real-robot rollouts (Sprint 11 ingest).
    known_pattern_ids
        Pattern ids that already exist in the v2 catalog — those
        traces flow through bridge_reweighter instead.
    min_trace_count
        Minimum trace count per pattern_id to consider it for
        promotion.  Single-shot anecdotes are dropped.
    promote_threshold
        Minimum placebo_adjusted_uplift needed to emit a candidate.
        Defaults to the same threshold Sprint 6 uses, so the two
        loops agree on what counts as "real improvement".

    Returns
    -------
    list[CandidatePattern]
        Sorted by ``(evidence_count desc, id)`` so output is
        deterministic.  Empty when no traces clear the gates.
    """
    known = set(known_pattern_ids)
    buckets: dict[str, list[EvidenceTrace]] = defaultdict(list)
    for t in traces:
        if t.pattern_id and t.pattern_id in known:
            continue
        buckets[_bucket_key(t)].append(t)

    candidates: list[CandidatePattern] = []
    for _key, group in buckets.items():
        if len(group) < min_trace_count:
            continue

        true_deltas = [t.best_delta_5 for t in group
                       if t.arm == "true" and t.best_delta_5 is not None]
        placebo_deltas = [t.best_delta_5 for t in group
                          if t.arm == "placebo" and t.best_delta_5 is not None]
        adj = _placebo_adjusted_uplift(true_deltas, placebo_deltas)
        if adj is None or adj < promote_threshold:
            continue

        pattern_id = group[0].pattern_id
        task_name = group[0].task_name
        candidate_id = _candidate_id_for(pattern_id, task_name)

        diff_phrases: list[str] = []
        for t in group:
            diff_phrases.extend(t.code_diff_summary)
        # Dedup while preserving order so the description is stable.
        seen: set[str] = set()
        unique_diffs = [d for d in diff_phrases if not (d in seen or seen.add(d))]

        diagnosis = (
            f"Real-robot trajectory extractor: pattern "
            f"{pattern_id or '<none>'} on task '{task_name}' showed "
            f"placebo-adjusted uplift = {adj:+.3f} across "
            f"{len(true_deltas)} true / {len(placebo_deltas)} placebo "
            f"rollouts.  Discovered by Sprint 11 self-improvement loop."
        )

        mutations = [
            Mutation(
                kind="other",
                description=(
                    f"Real-robot agent applied {pattern_id or 'an unnamed change'}; "
                    f"summary: {'; '.join(unique_diffs) if unique_diffs else 'no diff summary recorded'}"
                ),
                target_identifier=task_name or None,
            )
        ]

        candidates.append(CandidatePattern(
            id=candidate_id,
            task_family=_task_family_for(task_name),
            failure_id=None,  # Sprint 4 compile path can attach later
            diagnosis=diagnosis,
            successful_mutations=mutations,
            expected_verifier_signal=(
                "Real-robot rollout: post_score_5 mean significantly above "
                "placebo arm."
            ),
            evidence_count=len(group),
            avg_score_delta=adj,
            source_trajectory_ids=[t.trace_id for t in group],
        ))

    candidates.sort(key=lambda c: (-c.evidence_count, c.id))
    return candidates


__all__ = (
    "MIN_TRACE_COUNT",
    "PROMOTE_THRESHOLD",
    "extract_candidates_from_evidence_traces",
)
