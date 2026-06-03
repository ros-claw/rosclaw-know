"""Sprint 12: one-line ``RobotEvent → bridge_index update`` chain.

The full self-improvement loop in a single function call:

  RobotEvents
     ─► events_to_evidence_traces   (Sprint 11)
        ─► distill                  (Sprint 6)
           ─► reweight_bridge_index_from_stats  (Sprint 12)
              ─► bridge_index.json updated

Returned alongside the reweight summary is the coverage report from
:func:`evidence_distill.distill` so callers can surface Sprint 6's
gates (injection_id missing, post_score coverage, etc.) without
re-distilling.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ..bridge_reweighter import reweight_bridge_index_from_traces
from .event_schema import RobotEvent
from .event_to_evidence import events_to_evidence_traces

if TYPE_CHECKING:  # pragma: no cover
    from ..evidence_distill import CoverageReport

log = logging.getLogger("rosclaw_know.sim_ingest.bridge_direct")


def reweight_bridge_from_robot_events(
    events: Iterable[RobotEvent],
    *,
    bridge_path: Path | None = None,
    metrics_path: Path | None = None,
) -> tuple[dict[str, int], CoverageReport]:
    """End-to-end: real-robot events → bridge_index update.

    Parameters mirror :func:`bridge_reweighter.reweight_bridge_index_from_traces`.
    Events without a ``task_run`` envelope are silently dropped (they
    don't carry the score-arm metadata needed for an EvidenceTrace).

    Returns
    -------
    tuple[dict[str, int], CoverageReport]
        ``(summary, coverage)``.  ``summary`` is the reweighter's
        per-cluster count of touched / promoted / demoted / total.
        ``coverage`` is the Sprint 6 coverage report (violations
        list); when no traces are emitted this still contains the
        catalysts-with-X coverage rates so callers can detect
        "promote skipped because evidence too sparse".
    """
    traces = events_to_evidence_traces(events)
    log.info(
        "sprint 12 direct path: %d events → %d traces → reweight",
        sum(1 for _ in events) if hasattr(events, "__len__") else -1,  # type: ignore[arg-type]
        len(traces),
    )
    return reweight_bridge_index_from_traces(
        traces,
        bridge_path=bridge_path,
        metrics_path=metrics_path,
    )


__all__ = ("reweight_bridge_from_robot_events",)
