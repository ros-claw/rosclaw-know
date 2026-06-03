"""Sprint 9: MuJoCo rollout JSONL → :class:`RobotEvent` stream.

MuJoCo rollouts emitted from ``mujoco.MjData`` step-loops typically log
per-step values like::

    {
      "step": 5012,
      "wall_time": 12.34,
      "embodiment_id": "quadrotor",
      "qpos": [...],
      "qvel": [...],
      "ctrl": [...],
      "contact": [{"force": 18.0, "geom1": "rotor_a", "geom2": "wall"}],
      "events": ["nan_in_ctrl", "actuator_limit:0"]
    }

The reader recognises:

* ``contact`` array with force ≥ noise floor → ``collision``
* per-step ``events`` strings like ``"actuator_limit:i"`` or
  ``"nan_in_ctrl"`` → ``actuator_saturation`` / ``controller_error``
* ``follow_error`` (when present) > tolerance → ``trajectory_deviation``

If neither a ``contact`` array nor an ``events`` list is present, the
row is silently skipped — MuJoCo rollouts can be massive and most
rows are unremarkable.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .event_schema import RobotEvent

log = logging.getLogger("rosclaw_know.sim_ingest.mujoco")

_CONTACT_NOISE_FLOOR = 1.0  # N — below this is sub-mm vibration noise


def _sim_time(row: dict[str, Any]) -> str:
    t = row.get("wall_time", row.get("sim_time", row.get("step")))
    if isinstance(t, (int, float)):
        return f"ros_time:{float(t):.6f}"
    if isinstance(t, str):
        return t
    return ""


def _iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("mujoco jsonl: skipping malformed line %d: %s", lineno, exc)
                continue
            if not isinstance(obj, dict):
                continue
            yield obj


def _events_from_contacts(
    row: dict[str, Any], ts: str, embodiment: str, source_id: str
) -> list[RobotEvent]:
    contacts = row.get("contact") or row.get("contacts") or []
    if not isinstance(contacts, list):
        return []
    out: list[RobotEvent] = []
    for c in contacts:
        if not isinstance(c, dict):
            continue
        force = float(c.get("force", c.get("normal_force_mag", 0)) or 0)
        if force < _CONTACT_NOISE_FLOOR:
            continue
        geom1 = str(c.get("geom1") or c.get("body1") or c.get("link_a") or "")
        geom2 = str(c.get("geom2") or c.get("body2") or c.get("link_b") or "")
        out.append(
            RobotEvent(
                timestamp=ts,
                event_type="collision",
                embodiment_id=embodiment,
                severity="safety_critical" if force > 50.0 else "warning",
                fingerprint=f"collision::{geom1}::{geom2}".rstrip(":"),
                fields={"force": force, "geom1": geom1, "geom2": geom2},
                source="mujoco",
                source_id=source_id,
            )
        )
    return out


def _events_from_event_strings(
    row: dict[str, Any], ts: str, embodiment: str, source_id: str
) -> list[RobotEvent]:
    """Translate the per-step ``"events"`` string array."""
    evs = row.get("events") or []
    if not isinstance(evs, list):
        return []
    out: list[RobotEvent] = []
    for raw in evs:
        if not isinstance(raw, str):
            continue
        tag = raw.strip().lower()
        if not tag:
            continue
        # actuator_limit:<idx>
        if tag.startswith("actuator_limit"):
            idx = tag.split(":", 1)[1] if ":" in tag else "unknown"
            out.append(RobotEvent(
                timestamp=ts,
                event_type="actuator_saturation",
                embodiment_id=embodiment,
                severity="warning",
                fingerprint=f"saturation::actuator::{idx}",
                fields={"actuator_index": idx},
                source="mujoco",
                source_id=source_id,
            ))
            continue
        # nan_in_ctrl
        if tag in ("nan_in_ctrl", "nan_in_qpos", "nan_in_qvel", "policy_diverged"):
            out.append(RobotEvent(
                timestamp=ts,
                event_type="controller_error",
                embodiment_id=embodiment,
                severity="safety_critical",
                fingerprint=f"controller::nan::{tag}",
                fields={"event": tag},
                source="mujoco",
                source_id=source_id,
            ))
            continue
        # joint_limit:<name>
        if tag.startswith("joint_limit"):
            joint = tag.split(":", 1)[1] if ":" in tag else "unknown"
            out.append(RobotEvent(
                timestamp=ts,
                event_type="joint_limit_violation",
                embodiment_id=embodiment,
                severity="safety_critical",
                fingerprint=f"joint::{joint}",
                fields={"joint_name": joint},
                source="mujoco",
                source_id=source_id,
            ))
            continue
        # imu_spike
        if tag.startswith("imu_spike") or tag.startswith("sensor_dropout"):
            sensor = tag.split(":", 1)[1] if ":" in tag else "imu"
            out.append(RobotEvent(
                timestamp=ts,
                event_type="sensor_outlier",
                embodiment_id=embodiment,
                severity="warning",
                fingerprint=f"sensor::{sensor}::{tag.split(':')[0]}",
                fields={"sensor": sensor},
                source="mujoco",
                source_id=source_id,
            ))
            continue
        # rollout_timeout
        if tag == "rollout_timeout":
            out.append(RobotEvent(
                timestamp=ts,
                event_type="task_timeout",
                embodiment_id=embodiment,
                severity="warning",
                fingerprint="task::rollout_timeout",
                fields={"event": tag},
                source="mujoco",
                source_id=source_id,
            ))
    return out


def _events_from_follow_error(
    row: dict[str, Any], ts: str, embodiment: str, source_id: str
) -> list[RobotEvent]:
    err = row.get("follow_error")
    if err is None:
        return []
    try:
        err = float(err)
    except (TypeError, ValueError):
        return []
    tol = float(row.get("follow_tolerance", 0.05) or 0.05)
    if err <= tol:
        return []
    return [
        RobotEvent(
            timestamp=ts,
            event_type="trajectory_deviation",
            embodiment_id=embodiment,
            severity="warning",
            fingerprint=f"trajectory::{row.get('goal_id', 'goal')}",
            fields={"follow_error": err, "tolerance": tol},
            source="mujoco",
            source_id=source_id,
        )
    ]


def read_mujoco_jsonl(path: str | Path) -> list[RobotEvent]:
    """Parse a MuJoCo rollout JSONL → list of :class:`RobotEvent`."""
    p = Path(path)
    out: list[RobotEvent] = []
    src = str(p)
    for row in _iter_rows(p):
        embodiment = str(row.get("embodiment_id") or row.get("robot") or "default_embodiment")
        ts = _sim_time(row)
        out.extend(_events_from_contacts(row, ts, embodiment, src))
        out.extend(_events_from_event_strings(row, ts, embodiment, src))
        out.extend(_events_from_follow_error(row, ts, embodiment, src))
    return out
