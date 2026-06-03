"""Sprint 9: tests for Isaac Sim / MuJoCo / Foxglove adapters."""
from __future__ import annotations

import json
from pathlib import Path

from rosclaw_know.sim_ingest import (
    read_foxglove_jsonl,
    read_isaac_jsonl,
    read_mujoco_jsonl,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint9"


# ── Isaac Sim ───────────────────────────────────────────────────────────


def test_isaac_returns_events() -> None:
    evs = read_isaac_jsonl(FIX / "sample_isaac.jsonl")
    assert len(evs) >= 5
    for ev in evs:
        assert ev.source == "isaac_sim"


def test_isaac_covers_distinct_event_types() -> None:
    evs = read_isaac_jsonl(FIX / "sample_isaac.jsonl")
    seen = {ev.event_type for ev in evs}
    assert seen >= {
        "joint_limit_violation", "collision", "actuator_saturation",
        "controller_error", "task_timeout", "sensor_outlier",
    }


def test_isaac_task_terminated_success_is_skipped(tmp_path: Path) -> None:
    p = tmp_path / "i.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "ur5",
        "events": [{"type": "task_terminated", "reason": "success"}],
    }) + "\n")
    assert read_isaac_jsonl(p) == []


def test_isaac_unknown_event_type_ignored(tmp_path: Path) -> None:
    p = tmp_path / "i.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "ur5",
        "events": [{"type": "warp_speed_engaged"}],
    }) + "\n")
    assert read_isaac_jsonl(p) == []


def test_isaac_self_collision_maps_to_collision(tmp_path: Path) -> None:
    p = tmp_path / "i.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "ur5",
        "events": [{"type": "self_collision",
                     "body_a": "ur5/upper_arm_link",
                     "body_b": "ur5/forearm_link",
                     "force": 12.0}],
    }) + "\n")
    evs = read_isaac_jsonl(p)
    assert evs and evs[0].event_type == "collision"


def test_isaac_severity_override(tmp_path: Path) -> None:
    p = tmp_path / "i.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "ur5",
        "events": [{"type": "joint_limit", "joint": "j1", "severity": "info"}],
    }) + "\n")
    evs = read_isaac_jsonl(p)
    assert evs[0].severity == "info"


# ── MuJoCo ──────────────────────────────────────────────────────────────


def test_mujoco_returns_events() -> None:
    evs = read_mujoco_jsonl(FIX / "sample_mujoco.jsonl")
    assert len(evs) >= 5
    for ev in evs:
        assert ev.source == "mujoco"


def test_mujoco_actuator_limit_string_is_parsed(tmp_path: Path) -> None:
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "quadrotor",
        "events": ["actuator_limit:3"],
    }) + "\n")
    evs = read_mujoco_jsonl(p)
    assert evs and evs[0].event_type == "actuator_saturation"
    assert evs[0].fingerprint == "saturation::actuator::3"


def test_mujoco_nan_in_ctrl_is_safety_critical(tmp_path: Path) -> None:
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "ur5",
        "events": ["nan_in_ctrl"],
    }) + "\n")
    evs = read_mujoco_jsonl(p)
    assert evs and evs[0].severity == "safety_critical"


def test_mujoco_contact_below_floor_ignored(tmp_path: Path) -> None:
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "quadrotor",
        "contact": [{"force": 0.2, "geom1": "g1", "geom2": "g2"}],
    }) + "\n")
    assert read_mujoco_jsonl(p) == []


def test_mujoco_follow_error_within_tol_ignored(tmp_path: Path) -> None:
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps({
        "step": 1, "embodiment_id": "ur5",
        "follow_error": 0.01, "follow_tolerance": 0.05,
    }) + "\n")
    assert read_mujoco_jsonl(p) == []


# ── Foxglove ────────────────────────────────────────────────────────────


def test_foxglove_json_array_is_parsed() -> None:
    evs = read_foxglove_jsonl(FIX / "sample_foxglove.json")
    assert len(evs) == 4
    for ev in evs:
        assert ev.source == "foxglove"


def test_foxglove_categories_map_to_event_types() -> None:
    evs = read_foxglove_jsonl(FIX / "sample_foxglove.json")
    by_id = {ev.fields.get("annotation_id"): ev for ev in evs}
    assert by_id["ann_001"].event_type == "collision"
    assert by_id["ann_002"].event_type == "safety_stop"
    assert by_id["ann_002"].severity == "safety_critical"
    assert by_id["ann_003"].event_type == "controller_error"
    assert by_id["ann_004"].event_type == "sensor_outlier"


def test_foxglove_unknown_category_ignored(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_text(json.dumps({
        "id": "x", "category": "something_weird", "embodiment_id": "ur5",
    }) + "\n")
    assert read_foxglove_jsonl(p) == []


def test_foxglove_jsonl_format_also_works(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    rows = [
        {"id": "a", "category": "collision", "embodiment_id": "ur5",
          "metadata": {"object_a": "x", "object_b": "y"}},
        {"id": "b", "category": "estop", "embodiment_id": "ur5"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    evs = read_foxglove_jsonl(p)
    assert {ev.event_type for ev in evs} == {"collision", "safety_stop"}
