"""Sprint 9: Isaac Sim rollout JSONL → :class:`RobotEvent` stream.

Isaac Sim rollouts (Isaac Lab / Isaac Gym style) typically log per-step
JSONL with this rough shape::

    {
      "step": 1234,
      "sim_time": 12.34,
      "embodiment_id": "ur5",
      "events": [
        {"type": "collision", "body_a": "ur5/wrist", "body_b": "table",
         "force": 120.5},
        {"type": "joint_limit", "joint": "shoulder_pan", "delta": 0.04},
        {"type": "task_terminated", "reason": "timeout"}
      ],
      "rewards": {...},
      "obs": {...}
    }

Only the ``events`` array matters for ingest — observations and rewards
already flow through the existing :class:`schemas.EvidenceTrace`
pipeline.  The reader is intentionally tolerant: missing fields use
sensible defaults rather than throwing, because rollout logs from
hand-rolled wrappers are notoriously inconsistent.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .event_schema import RobotEvent

log = logging.getLogger("rosclaw_know.sim_ingest.isaac")


_ISAAC_EVENT_MAP: dict[str, tuple[str, str]] = {
    # Isaac vocabulary → (RobotEvent.event_type, default severity)
    "collision":            ("collision",              "warning"),
    "self_collision":       ("collision",              "warning"),
    "joint_limit":          ("joint_limit_violation",  "safety_critical"),
    "joint_violation":      ("joint_limit_violation",  "safety_critical"),
    "torque_saturation":    ("actuator_saturation",    "warning"),
    "velocity_saturation":  ("actuator_saturation",    "warning"),
    "sensor_dropout":       ("sensor_outlier",         "warning"),
    "imu_spike":            ("sensor_outlier",         "warning"),
    "task_terminated":      ("task_timeout",           "warning"),  # if reason=timeout
    "task_failed":          ("task_timeout",           "warning"),
    "policy_diverged":      ("controller_error",       "warning"),
    "trajectory_error":     ("trajectory_deviation",   "warning"),
}


def _sim_time(row: dict[str, Any]) -> str:
    """Return a ``ros_time:`` formatted timestamp for an Isaac row."""
    t = row.get("sim_time", row.get("time", row.get("step")))
    if isinstance(t, (int, float)):
        return f"ros_time:{float(t):.6f}"
    if isinstance(t, str):
        return t
    return ""


def _fingerprint(kind: str, payload: dict[str, Any]) -> str:
    """Build a stable fingerprint per Isaac event payload."""
    if kind in ("collision", "self_collision"):
        a = str(payload.get("body_a") or payload.get("body") or "")
        b = str(payload.get("body_b") or "")
        return f"collision::{a}::{b}".rstrip(":")
    if kind in ("joint_limit", "joint_violation"):
        return f"joint::{payload.get('joint', 'unknown')}"
    if kind in ("torque_saturation", "velocity_saturation"):
        sat_kind = "torque" if "torque" in kind else "velocity"
        return f"saturation::{sat_kind}::{payload.get('joint', payload.get('actuator', 'unknown'))}"
    if kind in ("sensor_dropout", "imu_spike"):
        return f"sensor::{payload.get('sensor', payload.get('name', 'unknown'))}::{kind}"
    if kind in ("task_terminated", "task_failed"):
        return f"task::{payload.get('task_name', payload.get('task', 'unknown'))}"
    if kind == "policy_diverged":
        return f"controller::{payload.get('policy', 'policy')}::diverged"
    if kind == "trajectory_error":
        return f"trajectory::{payload.get('goal_id', 'goal')}"
    return f"{kind}::generic"


def _iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("isaac jsonl: skipping malformed line %d: %s", lineno, exc)
                continue
            if not isinstance(obj, dict):
                continue
            yield obj


def read_isaac_jsonl(path: str | Path) -> list[RobotEvent]:
    """Parse an Isaac Sim rollout JSONL → list of :class:`RobotEvent`."""
    p = Path(path)
    out: list[RobotEvent] = []
    for row in _iter_rows(p):
        embodiment = str(row.get("embodiment_id") or row.get("robot") or "default_embodiment")
        ts = _sim_time(row)
        events_block = row.get("events", []) or []
        if not isinstance(events_block, list):
            continue
        for raw_ev in events_block:
            if not isinstance(raw_ev, dict):
                continue
            kind = str(raw_ev.get("type") or raw_ev.get("kind") or "")
            if kind not in _ISAAC_EVENT_MAP:
                continue
            # task_terminated only counts if the reason was a timeout.
            if kind in ("task_terminated", "task_failed"):
                if str(raw_ev.get("reason", "")).lower() not in ("timeout", "time_limit"):
                    continue
            event_type, default_severity = _ISAAC_EVENT_MAP[kind]
            sev = str(raw_ev.get("severity") or default_severity)
            if sev not in ("info", "warning", "safety_critical"):
                sev = default_severity
            fp = _fingerprint(kind, raw_ev)
            out.append(
                RobotEvent(
                    timestamp=ts,
                    event_type=event_type,  # type: ignore[arg-type]
                    embodiment_id=embodiment,
                    severity=sev,  # type: ignore[arg-type]
                    fingerprint=fp,
                    fields={k: v for k, v in raw_ev.items() if k not in ("type", "kind")},
                    source="isaac_sim",
                    source_id=str(p),
                )
            )
    return out
