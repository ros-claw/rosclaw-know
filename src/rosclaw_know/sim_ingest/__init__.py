"""Sprint 9: real-robot / sim log ingest.

Each submodule turns a raw observation source (rosbag, mcap, Foxglove,
Isaac Sim, MuJoCo, controller config, URDF) into the typed knowledge
objects defined in :mod:`rosclaw_know.schemas`.

The contract is uniform: every adapter emits a stream of
:class:`event_schema.RobotEvent` records, which are then mapped to
:class:`schemas.FailureMode` / :class:`schemas.EvidenceTrace` /
:class:`schemas.ConstraintPattern` / :class:`schemas.EmbodimentCard`
by :mod:`event_to_failure`, :mod:`event_to_evidence`, :mod:`urdf_parser`.

Why a uniform :class:`RobotEvent`:
  * adapters stay decoupled from the typed-knowledge schema;
  * the downstream mapper / dedup logic only ships once;
  * a Sprint 9 acceptance test can prove the *same* pattern
    (e.g. ``anti_windup``) surfaces from a quadrotor rosbag *and* an
    arm joint-PID Foxglove timeline — closing plan §Sprint 9's
    cross-embodiment reuse gate.

Plan §Sprint 9 acceptance gates
-------------------------------

1. Real/sim logs → :class:`FailureMode`.
2. Sandbox collision report → :class:`ConstraintPattern`.
3. Same pattern survives on two distinct embodiments.

The :mod:`cross_embodiment` module turns those gates into runnable
assertions.
"""
from __future__ import annotations

from .cross_embodiment import (
    CrossEmbodimentReport,
    PatternReuseRow,
    derive_pattern_transfer_table,
    load_default_transfer_table,
    run_cross_embodiment_check,
)
from .cross_embodiment import render_markdown as render_cross_embodiment_markdown
from .event_schema import EVENT_TYPES, RobotEvent
from .event_to_evidence import event_to_evidence_trace
from .event_to_failure import EventToFailureMapper, MappedFailure, map_events_to_failures
from .foxglove_reader import read_foxglove_jsonl
from .isaac_reader import read_isaac_jsonl
from .mujoco_reader import read_mujoco_jsonl
from .rosbag_reader import read_rosbag_jsonl
from .urdf_parser import (
    ControllerConfig,
    URDFDoc,
    URDFJoint,
    parse_controller_config,
    parse_urdf,
    urdf_to_constraints,
    urdf_to_embodiment,
)

__all__ = (
    "EVENT_TYPES",
    "RobotEvent",
    "EventToFailureMapper",
    "MappedFailure",
    "map_events_to_failures",
    "event_to_evidence_trace",
    "read_rosbag_jsonl",
    "read_foxglove_jsonl",
    "read_isaac_jsonl",
    "read_mujoco_jsonl",
    "URDFJoint",
    "URDFDoc",
    "ControllerConfig",
    "parse_urdf",
    "parse_controller_config",
    "urdf_to_embodiment",
    "urdf_to_constraints",
    "PatternReuseRow",
    "CrossEmbodimentReport",
    "derive_pattern_transfer_table",
    "load_default_transfer_table",
    "run_cross_embodiment_check",
    "render_cross_embodiment_markdown",
)
