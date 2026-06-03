"""Sprint 9: :class:`RobotEvent` → :class:`schemas.EvidenceTrace`.

When a real robot / sim run includes a *successful task* alongside a
*failure event*, we can turn the pairing into an
:class:`schemas.EvidenceTrace` so the evidence-loop V2 distiller
(Sprint 6) can include real-robot results in its placebo-adjusted
uplift calculations.

This is intentionally *narrower* than :mod:`event_to_failure`: not
every event becomes an EvidenceTrace, only events that carry the
``task_run`` envelope::

    RobotEvent(
        event_type="task_timeout",
        ...,
        fields={
            "run_id": "rosbag_2026_06_02_12_17",
            "task_name": "pick_apple_off_table",
            "iteration": 7,
            "pattern_id": "anti_windup",
            "strategy": "CATALYST",
            "pre_score": 0.41,
            "post_score_5": 0.62,
            "code_diff_summary": ["update PID limits", "add windup guard"],
            ...
        },
    )

That convention lets a "task replay" log emit both the symptom event
*and* the eventual outcome — and the evidence loop closes itself.
"""
from __future__ import annotations

import logging
from typing import Any

from ..schemas import EvidenceTrace
from .event_schema import RobotEvent

log = logging.getLogger("rosclaw_know.sim_ingest.event_to_evidence")


def _float_or(default: float, value: Any) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def event_to_evidence_trace(
    event: RobotEvent,
    *,
    run_id: str | None = None,
    iteration: int | None = None,
    task_name: str | None = None,
    pattern_id: str | None = None,
    objective_direction: str = "maximize",
) -> EvidenceTrace | None:
    """Convert one :class:`RobotEvent` into an :class:`EvidenceTrace`.

    Returns ``None`` if the event does not carry a ``task_run``
    envelope.  Callers can wire optional kwargs to override fields
    that aren't on the event itself (useful for batch ingest where
    a single ``run_id`` covers many events).
    """
    f = event.fields
    if not isinstance(f, dict):
        return None
    resolved_run_id = str(run_id or f.get("run_id") or "")
    resolved_task = str(task_name or f.get("task_name") or "")
    if not resolved_run_id or not resolved_task:
        return None

    # pre_score is mandatory for an EvidenceTrace; bail if missing.
    pre = _opt_float(f.get("pre_score"))
    if pre is None:
        return None

    iteration_val = iteration if iteration is not None else int(f.get("iteration", 0))
    strategy = str(f.get("strategy") or "NONE").upper()
    if strategy not in ("SAFETY", "FREE_EXPLORATION", "CATALYST", "NONE"):
        strategy = "NONE"
    arm = str(f.get("arm") or "true")
    if arm not in ("baseline", "true", "placebo", "shuffled"):
        arm = "true"
    direction = str(f.get("objective_direction") or objective_direction)
    if direction not in ("maximize", "minimize"):
        direction = "maximize"
    verifier_status = str(f.get("verifier_status") or "unknown")
    if verifier_status not in ("valid", "invalid", "crashed", "unknown"):
        verifier_status = "unknown"

    code_diff_summary = f.get("code_diff_summary") or []
    if not isinstance(code_diff_summary, list):
        code_diff_summary = [str(code_diff_summary)]
    code_diff_summary = [str(s) for s in code_diff_summary]
    hint_features = f.get("hint_features") or []
    if not isinstance(hint_features, list):
        hint_features = [str(hint_features)]
    hint_features = [str(s) for s in hint_features]

    trace = EvidenceTrace(
        trace_id=f"{resolved_run_id}::{event.event_type}::{iteration_val}",
        run_id=resolved_run_id,
        task_name=resolved_task,
        iteration=iteration_val,
        injection_id=(str(f.get("injection_id")) if f.get("injection_id") else None),
        pattern_id=pattern_id or (str(f.get("pattern_id")) if f.get("pattern_id") else None),
        strategy=strategy,  # type: ignore[arg-type]
        pre_score=pre,
        post_score_1=_opt_float(f.get("post_score_1")),
        post_score_3=_opt_float(f.get("post_score_3")),
        post_score_5=_opt_float(f.get("post_score_5")),
        best_delta_5=_opt_float(f.get("best_delta_5")),
        code_diff_summary=code_diff_summary,
        hint_features=hint_features,
        used_hint=bool(f.get("used_hint", False)),
        verifier_status=verifier_status,  # type: ignore[arg-type]
        objective_direction=direction,  # type: ignore[arg-type]
        arm=arm,  # type: ignore[arg-type]
        timestamp=event.timestamp,
    )
    return trace


__all__ = ("event_to_evidence_trace",)
