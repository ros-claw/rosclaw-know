"""Sprint 9: tests for the rosbag JSONL adapter."""
from __future__ import annotations

import json
from pathlib import Path

from rosclaw_know.sim_ingest import read_rosbag_jsonl
from rosclaw_know.sim_ingest.event_schema import EVENT_TYPES, RobotEvent

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sprint9" / "sample.rosbag.jsonl"


def test_returns_events_for_known_topics() -> None:
    evs = read_rosbag_jsonl(FIXTURE)
    assert len(evs) > 0
    for ev in evs:
        assert isinstance(ev, RobotEvent)
        assert ev.event_type in EVENT_TYPES
        assert ev.source == "rosbag"


def test_event_types_cover_six_categories() -> None:
    evs = read_rosbag_jsonl(FIXTURE)
    seen = {ev.event_type for ev in evs}
    # Fixture is hand-built to cover: safety_stop, collision,
    # joint_limit_violation, controller_error, trajectory_deviation,
    # task_timeout, sensor_outlier.  That's 7 distinct categories.
    assert seen >= {
        "safety_stop", "collision", "joint_limit_violation",
        "controller_error", "trajectory_deviation",
        "task_timeout", "sensor_outlier",
    }


def test_estop_only_fires_when_true(tmp_path: Path) -> None:
    p = tmp_path / "estop.jsonl"
    p.write_text(json.dumps({
        "topic": "/safety/e_stop", "data": {"data": False},
        "timestamp": "ros_time:1.0", "embodiment_id": "ur5",
    }) + "\n")
    assert read_rosbag_jsonl(p) == []


def test_contact_below_noise_floor_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "contact.jsonl"
    p.write_text(json.dumps({
        "topic": "/contacts",
        "timestamp": "ros_time:1.0",
        "embodiment_id": "ur5",
        "data": {"contacts": [{"normal_force_mag": 0.5,
                                "object_a": "ur5/wrist_3_link",
                                "object_b": "table"}]},
    }) + "\n")
    assert read_rosbag_jsonl(p) == []


def test_strong_contact_is_safety_critical(tmp_path: Path) -> None:
    p = tmp_path / "contact.jsonl"
    p.write_text(json.dumps({
        "topic": "/contacts",
        "timestamp": "ros_time:1.0",
        "embodiment_id": "ur5",
        "data": {"contacts": [{"normal_force_mag": 200.0,
                                "object_a": "ur5/wrist_3_link",
                                "object_b": "table"}]},
    }) + "\n")
    evs = read_rosbag_jsonl(p)
    assert len(evs) == 1
    assert evs[0].event_type == "collision"
    assert evs[0].severity == "safety_critical"


def test_joint_state_yields_per_joint_events(tmp_path: Path) -> None:
    p = tmp_path / "js.jsonl"
    p.write_text(json.dumps({
        "topic": "/r1/joint_states",  # suffix match across namespace
        "timestamp": "ros_time:1.0",
        "embodiment_id": "ur5",
        "data": {
            "name": ["j1", "j2", "j3"],
            "position": [3.5, 0.1, 4.0],
            "in_violation": [True, False, True],
        },
    }) + "\n")
    evs = read_rosbag_jsonl(p)
    names = sorted(ev.fields["joint_name"] for ev in evs)
    assert names == ["j1", "j3"]
    for ev in evs:
        assert ev.event_type == "joint_limit_violation"
        assert ev.severity == "safety_critical"


def test_trajectory_within_tolerance_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({
        "topic": "/trajectory_status",
        "timestamp": "ros_time:1.0",
        "embodiment_id": "ur5",
        "data": {"goal_id": "x", "follow_error": 0.01, "tolerance": 0.05},
    }) + "\n")
    assert read_rosbag_jsonl(p) == []


def test_unknown_topic_is_silently_skipped(tmp_path: Path) -> None:
    p = tmp_path / "u.jsonl"
    p.write_text(json.dumps({
        "topic": "/some/internal_diag",
        "timestamp": "ros_time:1.0",
        "embodiment_id": "ur5",
        "data": {"data": True},
    }) + "\n")
    assert read_rosbag_jsonl(p) == []


def test_malformed_lines_are_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "m.jsonl"
    p.write_text(
        "this is not json\n"
        + json.dumps({
            "topic": "/safety/e_stop", "data": {"data": True},
            "timestamp": "ros_time:1.0", "embodiment_id": "ur5",
        }) + "\n"
        + "{not closed"
    )
    evs = read_rosbag_jsonl(p)
    assert len(evs) == 1


def test_stable_key_is_embodiment_agnostic(tmp_path: Path) -> None:
    p = tmp_path / "wu.jsonl"
    p.write_text(
        json.dumps({"topic": "/controller_state", "timestamp": "t1",
                     "embodiment_id": "ur5",
                     "data": {"status": "windup", "controller": "pid"}}) + "\n"
        + json.dumps({"topic": "/uav/controller_state", "timestamp": "t2",
                       "embodiment_id": "quadrotor",
                       "data": {"status": "windup", "controller": "pid"}}) + "\n"
    )
    evs = read_rosbag_jsonl(p)
    assert len(evs) == 2
    # Same fingerprint → same stable_key, despite different embodiment.
    assert evs[0].stable_key()[2] == evs[1].stable_key()[2]


def test_embodiment_id_default(tmp_path: Path) -> None:
    p = tmp_path / "noemb.jsonl"
    p.write_text(json.dumps({
        "topic": "/safety/e_stop", "data": {"data": True},
        "timestamp": "ros_time:1.0",
    }) + "\n")
    evs = read_rosbag_jsonl(p)
    assert evs[0].embodiment_id == "default_embodiment"
