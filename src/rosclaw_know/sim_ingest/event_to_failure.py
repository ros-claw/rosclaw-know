"""Sprint 9: :class:`RobotEvent` → :class:`schemas.FailureMode`.

Why this layer exists
---------------------

The adapters in :mod:`sim_ingest` keep their schema *close to the raw
log shape*.  Downstream callers (graph builder, retriever, evidence
distiller) instead want canonical :class:`schemas.FailureMode`
records.  Mapping is the one place where:

* Multiple raw events sharing a ``stable_key`` collapse into a single
  FailureMode (the count goes into ``observable_signals``).
* The 8-category :data:`event_schema.EVENT_TYPES` enum is translated
  into FailureMode ``id``, ``name``, ``symptom_text`` etc. — the
  things the hybrid retriever can actually search over.
* :data:`schemas.FRONTIER_DOMAINS` is enforced (mapper rejects events
  whose ``embodiment_id`` is unknown only after attaching a fallback
  domain — never silently drops them).

Plan §Sprint 9 acceptance:

* every event_type produces at least one mapped FailureMode in the
  reference fixture set;
* events from *different* embodiments but the *same* failure semantics
  collapse to **one** FailureMode (this is the cross-embodiment
  reuse gate);
* the mapped FailureMode validates against :class:`schemas.FailureMode`
  via pydantic.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..schemas import FRONTIER_DOMAINS, FailureMode
from .event_schema import RobotEvent, Severity

log = logging.getLogger("rosclaw_know.sim_ingest.event_to_failure")

# Embodiment id → canonical domain bucket.  Picked from the v1.5
# embodiments.yaml + FRONTIER_DOMAINS taxonomy.
_EMBODIMENT_TO_DOMAIN: dict[str, str] = {
    "ur5":              "Control_Locomotion",
    "manipulator":      "Control_Locomotion",
    "kuka":             "Control_Locomotion",
    "xarm":             "Control_Locomotion",
    "arm":              "Control_Locomotion",
    "unitree_g1":       "Control_Locomotion",
    "unitree_go2":      "Control_Locomotion",
    "spot":             "Control_Locomotion",
    "humanoid":         "Control_Locomotion",
    "quadruped":        "Control_Locomotion",
    "quadrotor":        "Control_Locomotion",
    "uav":              "Control_Locomotion",
    "drone":            "Control_Locomotion",
    "turtlebot":        "Control_Locomotion",
    "wheeled_robot":    "Control_Locomotion",
    "gpu_kernel":       "Systems_Compute",
    "data_center":      "Systems_Compute",
    "optical_system":   "Perception_Vision",
    "camera":           "Perception_Vision",
    "lidar":            "Perception_Vision",
    "battery":          "Energy_Power",
    "default_embodiment": "World_Physics",  # safe-but-non-empty default
}

# event_type → (id_stem, default_name, default_symptom)
_EVENT_TO_FAILURE: dict[str, tuple[str, str, str]] = {
    "collision": (
        "collision_with_environment",
        "Collision with environment",
        "Robot link made unintended contact with environment object.",
    ),
    "safety_stop": (
        "safety_stop_triggered",
        "Safety stop triggered",
        "E-stop or safety controller halted execution mid-trajectory.",
    ),
    "joint_limit_violation": (
        "joint_limit_violation",
        "Joint outside permitted range",
        "Joint position or torque exceeded the URDF / controller limit.",
    ),
    "controller_error": (
        "controller_divergence",
        "Controller diverged or NaN-ed",
        "Controller output went NaN, windup, or otherwise failed integrity check.",
    ),
    "sensor_outlier": (
        "sensor_outlier",
        "Sensor produced anomalous reading",
        "Sensor channel returned a value outside the expected envelope (dropout or spike).",
    ),
    "task_timeout": (
        "task_timeout",
        "Task exceeded time budget",
        "Task terminated by the harness because elapsed time exceeded the configured budget.",
    ),
    "trajectory_deviation": (
        "trajectory_follow_error",
        "Trajectory follow error",
        "Realised pose deviated from planned trajectory beyond the configured tolerance.",
    ),
    "actuator_saturation": (
        "actuator_saturation",
        "Actuator command saturated",
        "Commanded torque or velocity hit the actuator limit and remained pegged.",
    ),
}

_ID_SAFE_RE = re.compile(r"[^a-z0-9_]+")


def _safe_id_chunk(s: str) -> str:
    return _ID_SAFE_RE.sub("_", s.lower()).strip("_")


def _domain_for(embodiment_id: str) -> str:
    """Return the canonical domain for an embodiment id.

    Falls through ``"<vendor>_<chassis>"`` patterns by trying any
    prefix split — e.g. ``"ur5_arm_a"`` → matches ``"ur5"`` → returns
    ``"Control_Locomotion"``.
    """
    if embodiment_id in _EMBODIMENT_TO_DOMAIN:
        return _EMBODIMENT_TO_DOMAIN[embodiment_id]
    lower = embodiment_id.lower()
    for key, domain in _EMBODIMENT_TO_DOMAIN.items():
        if lower.startswith(key + "_") or lower == key:
            return domain
    return "World_Physics"


def _id_for(event: RobotEvent) -> str:
    """Stable :class:`FailureMode.id` for an event family.

    Identity rule (cross-embodiment dedup): only ``event_type +
    fingerprint`` contributes.  ``embodiment_id`` does **not** —
    that's what makes ``anti_windup`` collapse across UR5 and
    quadrotor.
    """
    stem = _EVENT_TO_FAILURE[event.event_type][0]
    extra = _safe_id_chunk(event.fingerprint) if event.fingerprint else ""
    if extra and not extra.startswith(stem):
        return f"failure_{stem}_{extra}"
    return f"failure_{stem}"


# ── public dataclass: mapped failure + provenance ───────────────────────


@dataclass(frozen=True)
class MappedFailure:
    """A :class:`FailureMode` plus the events it was distilled from."""

    failure: FailureMode
    source_events: tuple[RobotEvent, ...]
    embodiments_seen: tuple[str, ...]
    occurrence_count: int


# ── core mapper ─────────────────────────────────────────────────────────


class EventToFailureMapper:
    """Stateful mapper that dedups across events.

    Use this when you want to ingest many adapter streams and keep one
    canonical FailureMode per event family.  Stateless callers can use
    :func:`map_events_to_failures` instead.
    """

    def __init__(self) -> None:
        # key (event_type, fingerprint) → working state
        self._buckets: dict[tuple[str, str], dict] = {}

    def ingest(self, event: RobotEvent) -> None:
        """Add one event into the running buckets."""
        if event.event_type not in _EVENT_TO_FAILURE:
            log.debug("event_to_failure: skipping unknown event_type %s", event.event_type)
            return
        key = (event.event_type, event.fingerprint)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = {
                "events": [event],
                "embodiments": [event.embodiment_id],
                "max_severity": event.severity,
                "signals": [],
            }
            self._collect_signals(bucket, event)
            return
        bucket["events"].append(event)
        if event.embodiment_id not in bucket["embodiments"]:
            bucket["embodiments"].append(event.embodiment_id)
        bucket["max_severity"] = _max_severity(bucket["max_severity"], event.severity)
        self._collect_signals(bucket, event)

    def emit(self) -> list[MappedFailure]:
        """Finalise buckets into :class:`MappedFailure` records."""
        out: list[MappedFailure] = []
        for (event_type, _fp), bucket in self._buckets.items():
            events: list[RobotEvent] = bucket["events"]
            primary = events[0]
            id_stem, default_name, default_symptom = _EVENT_TO_FAILURE[event_type]
            failure_id = _id_for(primary)
            embodiments: list[str] = bucket["embodiments"]
            domain = _domain_for(embodiments[0])
            severity: Severity = bucket["max_severity"]
            name = default_name if not primary.fingerprint else (
                f"{default_name} ({primary.fingerprint})"
            )
            symptom_text = default_symptom
            signals = _dedup_keep_order(bucket["signals"])
            likely_causes = _likely_causes_for(event_type)
            contra = _contraindications_for(event_type)
            failure = FailureMode(
                id=failure_id,
                name=name,
                domain=domain,
                symptom_text=symptom_text,
                normalized_symptom=f"{event_type}::{primary.fingerprint or 'generic'}",
                observable_signals=signals,
                likely_causes=likely_causes,
                contraindications=contra,
                severity=severity,
            )
            out.append(MappedFailure(
                failure=failure,
                source_events=tuple(events),
                embodiments_seen=tuple(embodiments),
                occurrence_count=len(events),
            ))
        out.sort(key=lambda mf: mf.failure.id)
        return out

    @staticmethod
    def _collect_signals(bucket: dict, event: RobotEvent) -> None:
        """Append human-readable signal strings for FailureMode.observable_signals."""
        for k, v in event.fields.items():
            if k in ("source_topic", "label", "annotation_id"):
                continue
            if isinstance(v, (int, float, str, bool)) and v is not None:
                bucket["signals"].append(f"{k}={v}")


def _dedup_keep_order(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


_SEV_ORDER = {"info": 0, "warning": 1, "safety_critical": 2}


def _max_severity(a: Severity, b: Severity) -> Severity:
    return a if _SEV_ORDER[a] >= _SEV_ORDER[b] else b


def _likely_causes_for(event_type: str) -> list[str]:
    # Compact lookup; not exhaustive — these surface in
    # PatternCardV2.symptom rendering only.
    return {
        "collision": [
            "Insufficient obstacle margin",
            "Stale environment map",
            "Velocity / acceleration limit too aggressive",
        ],
        "safety_stop": [
            "Operator-triggered e-stop",
            "Force/torque safety threshold exceeded",
            "Watchdog timeout",
        ],
        "joint_limit_violation": [
            "Trajectory generator did not clamp to URDF limits",
            "IK solver returned out-of-range solution",
            "PID windup pushed actuator past limit",
        ],
        "controller_error": [
            "Integral windup",
            "Numerical instability (Δt too large, NaN in gains)",
            "Unmodelled disturbance",
        ],
        "sensor_outlier": [
            "Hardware fault / loose cable",
            "Lighting / surface condition change",
            "Drift not compensated",
        ],
        "task_timeout": [
            "Exploration plan too greedy",
            "Verifier slower than budget allows",
            "Plan repeatedly retries the same dead-end",
        ],
        "trajectory_deviation": [
            "Tracking gains too soft",
            "Friction / payload model mismatch",
            "Latency in feedback loop",
        ],
        "actuator_saturation": [
            "Aggressive feedforward command",
            "Insufficient gear ratio for required torque",
            "Multiple controllers competing",
        ],
    }.get(event_type, [])


def _contraindications_for(event_type: str) -> list[str]:
    return {
        "collision": ["Do not retry the failing trajectory without updating the world model."],
        "safety_stop": ["Do not auto-resume without operator clearance."],
        "joint_limit_violation": [
            "Do not just clamp inside the controller — fix the planner."
        ],
        "controller_error": ["Do not raise integral gain to mask divergence."],
        "sensor_outlier": ["Do not blindly average across an outlier — flag and replan."],
        "task_timeout": ["Do not extend the budget without diagnosing why."],
        "trajectory_deviation": ["Do not stiffen gains without checking payload model."],
        "actuator_saturation": ["Do not push commands past the saturation limit."],
    }.get(event_type, [])


# ── stateless convenience ───────────────────────────────────────────────


def map_events_to_failures(events: Iterable[RobotEvent]) -> list[MappedFailure]:
    """One-shot mapper for callers that already have all events in memory."""
    mapper = EventToFailureMapper()
    for ev in events:
        mapper.ingest(ev)
    return mapper.emit()


# Re-export for tests
__all__ = (
    "EventToFailureMapper",
    "MappedFailure",
    "map_events_to_failures",
    "FRONTIER_DOMAINS",
)
