"""Sprint 9: uniform :class:`RobotEvent` envelope.

Every Sprint 9 adapter (rosbag / mcap / Foxglove / Isaac / MuJoCo)
emits the same compact event shape so the downstream mapper does not
need to care which source the record came from.  Adapters stay tiny
(format → :class:`RobotEvent`) and mappers stay generic
(:class:`RobotEvent` → :class:`schemas.FailureMode` /
:class:`schemas.EvidenceTrace`).

The dataclass is **frozen** so events compare structurally — this
matters because the dedup step in :func:`event_to_failure.map_events`
relies on ``(event_type, embodiment_id, fingerprint)`` equality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# The 8 canonical event categories Sprint 9 understands.  Picking a
# closed enum keeps mapper coverage trivially checkable in tests.
EVENT_TYPES: tuple[str, ...] = (
    "collision",            # contact > threshold or contact in forbidden region
    "safety_stop",          # e-stop pressed / safety controller triggered
    "joint_limit_violation",  # |position| > urdf joint limit (or torque > limit)
    "controller_error",     # controller diverged / PID windup / NaN
    "sensor_outlier",       # IMU spike / LiDAR dropout / vision lost track
    "task_timeout",         # task exceeded time budget without success
    "trajectory_deviation",  # follow-error > tolerance over N steps
    "actuator_saturation",  # torque / velocity hit the limit and stayed
)

EventType = Literal[
    "collision",
    "safety_stop",
    "joint_limit_violation",
    "controller_error",
    "sensor_outlier",
    "task_timeout",
    "trajectory_deviation",
    "actuator_saturation",
]

Severity = Literal["info", "warning", "safety_critical"]


@dataclass(frozen=True)
class RobotEvent:
    """One observation from a real robot or simulator.

    Attributes
    ----------
    timestamp
        ROS-time string (``ros_time:secs.nsecs`` preferred) or sim wall
        time.  Treated as opaque text for ordering.
    event_type
        One of :data:`EVENT_TYPES`.  Closed enum on purpose.
    embodiment_id
        The :class:`schemas.EmbodimentCard.id` this event happened on
        (e.g. ``"ur5"``, ``"unitree_g1"``, ``"quadrotor"``).  Allows
        cross-embodiment dedup later.
    severity
        ``"info" | "warning" | "safety_critical"``.  Promoted to
        :class:`schemas.FailureMode.severity` on the mapping side.
    fingerprint
        A short, stable identifier for *what kind of* event this is
        (e.g. ``"joint_shoulder_pan_upper"``).  Two events with the
        same ``(event_type, embodiment_id, fingerprint)`` collapse to a
        single FailureMode with bumped frequency.
    fields
        Adapter-specific extras: signal name, magnitude, threshold,
        sensor id, etc.  Used to render
        :attr:`schemas.FailureMode.observable_signals`.
    source
        Origin format tag — one of ``"rosbag" | "foxglove" |
        "isaac_sim" | "mujoco" | "controller_log"``.  Useful for
        :class:`schemas.SourceRecordV2`.
    source_id
        Path or topic from which this event was read.  Free-form.

    The class is immutable (frozen) so events can be put in a ``set()``
    for dedup, and so callers cannot mutate fields after emission.
    """

    timestamp: str
    event_type: EventType
    embodiment_id: str
    severity: Severity = "warning"
    fingerprint: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    source: str = "rosbag"
    source_id: str = ""

    def stable_key(self) -> tuple[str, str, str]:
        """Identity used by :mod:`event_to_failure` to dedup events.

        Two raw events that boil down to the same FailureMode share
        the same stable key.  Timestamps, signal magnitudes etc. vary
        per event and should *not* be part of the key.
        """
        return (self.event_type, self.embodiment_id, self.fingerprint)

    def to_jsonable(self) -> dict[str, Any]:
        """Return a JSON-serialisable view (for snapshotting tests)."""
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "embodiment_id": self.embodiment_id,
            "severity": self.severity,
            "fingerprint": self.fingerprint,
            "fields": dict(self.fields),
            "source": self.source,
            "source_id": self.source_id,
        }
