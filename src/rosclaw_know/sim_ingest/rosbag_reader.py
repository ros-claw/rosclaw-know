"""Sprint 9: rosbag / mcap → :class:`RobotEvent` stream.

We **do not** depend on the heavy ``rosbag2_py`` / ``mcap`` C extensions
because the only environment that matters for v1.5 ingest is the
ROSClaw evidence-loop CI, which runs in a generic Python container.

Instead we standardise on a *flat JSONL* representation that any
operator can produce with:

.. code-block:: bash

    mcap cat my_bag.mcap --json > my_bag.jsonl
    # or
    ros2 bag info --print-json my_bag/ > my_bag.jsonl

Each line is one ROS message snapshot::

    {
      "topic": "/safety/e_stop",
      "msg_type": "std_msgs/Bool",
      "timestamp": "ros_time:1717459200.250",
      "data": {"data": true},
      "embodiment_id": "ur5"     # optional; falls back to "default_embodiment"
    }

The reader recognises eight canonical topic patterns; everything else
is silently ignored (which is the right default for noisy production
bags).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .event_schema import RobotEvent

log = logging.getLogger("rosclaw_know.sim_ingest.rosbag")

# Per-topic dispatch table.  Each entry maps a topic *suffix* (so
# ``/r1/safety/e_stop`` and ``/r2/safety/e_stop`` both match) to a
# handler that returns a :class:`RobotEvent` or ``None`` to skip.
#
# Why suffix-match: real fleet setups namespace topics by robot
# (e.g. ``/ur5_a/cmd_vel``) — Sprint 9 needs to ingest those without
# requiring perfectly canonical topic names.

_DEFAULT_EMBODIMENT = "default_embodiment"


def _ros_t(msg: dict[str, Any]) -> str:
    ts = msg.get("timestamp")
    if isinstance(ts, (int, float)):
        return f"ros_time:{float(ts):.6f}"
    if isinstance(ts, str):
        return ts
    return ""


def _emb(msg: dict[str, Any]) -> str:
    return str(msg.get("embodiment_id") or _DEFAULT_EMBODIMENT)


def _suffix_match(topic: str, suffix: str) -> bool:
    """Match ``"…/safety/e_stop"`` regardless of robot namespace."""
    return topic == suffix or topic.endswith(suffix) or topic.endswith(suffix + "/")


def _handle_e_stop(msg: dict[str, Any]) -> RobotEvent | None:
    data = msg.get("data", {}) or {}
    pressed = bool(data.get("data"))
    if not pressed:
        return None
    return RobotEvent(
        timestamp=_ros_t(msg),
        event_type="safety_stop",
        embodiment_id=_emb(msg),
        severity="safety_critical",
        fingerprint="emergency_stop",
        fields={"source_topic": msg.get("topic", ""), **data},
        source="rosbag",
        source_id=str(msg.get("topic", "")),
    )


def _handle_contacts(msg: dict[str, Any]) -> RobotEvent | None:
    data = msg.get("data", {}) or {}
    contacts = data.get("contacts") or data.get("states") or []
    if not contacts:
        return None
    # Pick the strongest contact for fingerprint stability.
    worst = max(
        contacts,
        key=lambda c: float((c or {}).get("normal_force_mag", c.get("force_mag", 0)) or 0),
    )
    force_mag = float(worst.get("normal_force_mag", worst.get("force_mag", 0)) or 0)
    if force_mag < 5.0:  # noise floor — ignore brushing
        return None
    object_a = str(worst.get("object_a") or worst.get("link_a") or "")
    object_b = str(worst.get("object_b") or worst.get("link_b") or "")
    return RobotEvent(
        timestamp=_ros_t(msg),
        event_type="collision",
        embodiment_id=_emb(msg),
        severity="safety_critical" if force_mag > 80.0 else "warning",
        fingerprint=f"collision::{object_a}::{object_b}".rstrip(":"),
        fields={
            "force_mag": force_mag,
            "object_a": object_a,
            "object_b": object_b,
            "source_topic": msg.get("topic", ""),
        },
        source="rosbag",
        source_id=str(msg.get("topic", "")),
    )


def _handle_joint_state(msg: dict[str, Any]) -> Iterator[RobotEvent]:
    data = msg.get("data", {}) or {}
    names = data.get("name") or []
    positions = data.get("position") or []
    velocities = data.get("velocity") or []
    efforts = data.get("effort") or []
    # Embedded limit-violation flag (some controllers publish a
    # parallel ``in_violation`` array; if present, use it directly).
    flags = data.get("in_violation") or [False] * len(names)
    for i, name in enumerate(names):
        if i >= len(flags) or not flags[i]:
            continue
        pos = positions[i] if i < len(positions) else None
        vel = velocities[i] if i < len(velocities) else None
        eff = efforts[i] if i < len(efforts) else None
        yield RobotEvent(
            timestamp=_ros_t(msg),
            event_type="joint_limit_violation",
            embodiment_id=_emb(msg),
            severity="safety_critical",
            fingerprint=f"joint::{name}",
            fields={
                "joint_name": name,
                "position": pos,
                "velocity": vel,
                "effort": eff,
                "source_topic": msg.get("topic", ""),
            },
            source="rosbag",
            source_id=str(msg.get("topic", "")),
        )


def _handle_controller_state(msg: dict[str, Any]) -> RobotEvent | None:
    data = msg.get("data", {}) or {}
    status = str(data.get("status") or data.get("level") or "").lower()
    if status not in {"error", "failure", "diverged", "windup"}:
        return None
    return RobotEvent(
        timestamp=_ros_t(msg),
        event_type="controller_error",
        embodiment_id=_emb(msg),
        severity="warning",
        fingerprint=f"controller::{data.get('controller', 'unknown')}::{status}",
        fields={
            "controller": data.get("controller", "unknown"),
            "status": status,
            "error_norm": data.get("error_norm"),
            "source_topic": msg.get("topic", ""),
        },
        source="rosbag",
        source_id=str(msg.get("topic", "")),
    )


def _handle_sensor_alert(msg: dict[str, Any]) -> RobotEvent | None:
    data = msg.get("data", {}) or {}
    sensor = str(data.get("sensor") or data.get("name") or "unknown")
    kind = str(data.get("kind") or "outlier")
    return RobotEvent(
        timestamp=_ros_t(msg),
        event_type="sensor_outlier",
        embodiment_id=_emb(msg),
        severity="warning",
        fingerprint=f"sensor::{sensor}::{kind}",
        fields={
            "sensor": sensor,
            "kind": kind,
            "value": data.get("value"),
            "source_topic": msg.get("topic", ""),
        },
        source="rosbag",
        source_id=str(msg.get("topic", "")),
    )


def _handle_trajectory_status(msg: dict[str, Any]) -> RobotEvent | None:
    data = msg.get("data", {}) or {}
    error = float(data.get("follow_error", 0) or 0)
    if error <= float(data.get("tolerance", 0.05) or 0.05):
        return None
    return RobotEvent(
        timestamp=_ros_t(msg),
        event_type="trajectory_deviation",
        embodiment_id=_emb(msg),
        severity="warning",
        fingerprint=f"trajectory::{data.get('goal_id', 'unknown')}",
        fields={
            "goal_id": data.get("goal_id"),
            "follow_error": error,
            "tolerance": data.get("tolerance"),
            "source_topic": msg.get("topic", ""),
        },
        source="rosbag",
        source_id=str(msg.get("topic", "")),
    )


def _handle_task_status(msg: dict[str, Any]) -> RobotEvent | None:
    data = msg.get("data", {}) or {}
    outcome = str(data.get("outcome") or data.get("status") or "").lower()
    if outcome != "timeout":
        return None
    return RobotEvent(
        timestamp=_ros_t(msg),
        event_type="task_timeout",
        embodiment_id=_emb(msg),
        severity="warning",
        fingerprint=f"task::{data.get('task_name', 'unknown')}",
        fields={
            "task_name": data.get("task_name"),
            "elapsed_seconds": data.get("elapsed_seconds"),
            "budget_seconds": data.get("budget_seconds"),
            "source_topic": msg.get("topic", ""),
        },
        source="rosbag",
        source_id=str(msg.get("topic", "")),
    )


# (topic_suffix, handler) — keep first-match; handlers may yield zero
# or one event each, except joint_states which yields per-joint.
_TOPIC_HANDLERS: tuple[tuple[str, Any], ...] = (
    ("/safety/e_stop",        _handle_e_stop),
    ("/safety/estop",         _handle_e_stop),
    ("/contacts",             _handle_contacts),
    ("/collision/contact",    _handle_contacts),
    ("/contact_state",        _handle_contacts),
    ("/controller_state",     _handle_controller_state),
    ("/controller_status",    _handle_controller_state),
    ("/sensor_alert",         _handle_sensor_alert),
    ("/trajectory_status",    _handle_trajectory_status),
    ("/task_status",          _handle_task_status),
)


def _iter_messages(path: Path) -> Iterable[dict[str, Any]]:
    """Yield JSONL messages from ``path``, skipping malformed lines.

    Malformed JSON gets a single ``logger.warning`` and moves on —
    real bags have transient corruption and we shouldn't lose the
    entire ingest over one bad row.
    """
    with path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("rosbag jsonl: skipping malformed line %d: %s", lineno, exc)
                continue
            if not isinstance(obj, dict):
                continue
            yield obj


def read_rosbag_jsonl(path: str | Path) -> list[RobotEvent]:
    """Read a rosbag-jsonl file and emit a list of :class:`RobotEvent`.

    Joint-state messages can produce multiple events per row (one per
    joint in violation) — they are all collected in order.

    Returns
    -------
    list[RobotEvent]
        Order matches the file order.  Empty if the bag has nothing
        actionable.
    """
    p = Path(path)
    out: list[RobotEvent] = []
    for msg in _iter_messages(p):
        topic = str(msg.get("topic", ""))

        # joint_states is special: one row yields N events.  Handle
        # before the dispatch table so the "handler returns one event"
        # convention is preserved for everything else.
        if _suffix_match(topic, "/joint_states") or _suffix_match(topic, "/joint_state"):
            for ev in _handle_joint_state(msg):
                out.append(ev)
            continue

        for suffix, handler in _TOPIC_HANDLERS:
            if _suffix_match(topic, suffix):
                ev = handler(msg)
                if ev is not None:
                    out.append(ev)
                break
    return out
