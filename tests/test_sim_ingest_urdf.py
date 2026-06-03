"""Sprint 9: tests for the URDF + controller config parsers."""
from __future__ import annotations

from pathlib import Path

import pytest

from rosclaw_know.schemas import ConstraintPattern, EmbodimentCard
from rosclaw_know.sim_ingest import (
    parse_controller_config,
    parse_urdf,
    urdf_to_constraints,
    urdf_to_embodiment,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint9"


# ── URDF parsing ────────────────────────────────────────────────────────


def test_parse_urdf_finds_joints() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    assert doc.robot_name == "ur5"
    names = [j.name for j in doc.joints]
    assert "shoulder_pan_joint" in names
    assert "elbow_joint" in names
    assert "ft_sensor_joint" in names  # fixed joint included in URDFDoc
    assert len(doc.joints) == 7


def test_parse_urdf_skips_no_limit() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    ft = next(j for j in doc.joints if j.name == "ft_sensor_joint")
    assert ft.joint_type == "fixed"
    assert ft.lower is None and ft.upper is None


def test_parse_urdf_reads_limits() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    sp = next(j for j in doc.joints if j.name == "shoulder_pan_joint")
    assert sp.lower == pytest.approx(-3.14159)
    assert sp.upper == pytest.approx(3.14159)
    assert sp.effort == pytest.approx(150.0)
    assert sp.velocity == pytest.approx(3.15)


def test_parse_urdf_extracts_sensors_and_transmissions() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    assert "wrist_ft_sensor" in doc.sensors
    assert "front_realsense" in doc.sensors
    assert "shoulder_pan_trans" in doc.transmissions


# ── Controller config ──────────────────────────────────────────────────


def test_parse_controller_config_joint_to_controller() -> None:
    cfg = parse_controller_config(FIX / "controller_config.yaml")
    assert cfg.joint_to_controller["shoulder_pan_joint"] in (
        "joint_trajectory_controller", "velocity_controller",
    )
    # 6 joints share joint_trajectory_controller; shoulder_pan also has
    # velocity_controller — last writer wins, both are acceptable.
    assert cfg.joint_to_controller["wrist_3_joint"] == "joint_trajectory_controller"


def test_parse_controller_config_joint_limits_override() -> None:
    cfg = parse_controller_config(FIX / "controller_config.yaml")
    sp = cfg.joint_limit_overrides["shoulder_pan_joint"]
    assert sp["max_velocity"] == pytest.approx(2.5)
    assert sp["max_effort"] == pytest.approx(100.0)


# ── URDF → EmbodimentCard ──────────────────────────────────────────────


def test_urdf_to_embodiment_for_ur5() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    card = urdf_to_embodiment(doc)
    assert isinstance(card, EmbodimentCard)
    assert card.id == "ur5"
    assert card.embodiment_type == "manipulator"
    assert "shoulder_pan_joint" in card.actuators
    assert "wrist_ft_sensor" in card.sensors
    # safety_constraints text is human readable
    assert any("shoulder_pan_joint.position" in s for s in card.safety_constraints)


def test_urdf_to_embodiment_uses_controller_interfaces() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    cfg = parse_controller_config(FIX / "controller_config.yaml")
    card = urdf_to_embodiment(doc, controller=cfg)
    assert "joint_trajectory_controller" in card.control_interfaces
    assert "velocity_controller" in card.control_interfaces


# ── URDF → ConstraintPattern[] ─────────────────────────────────────────


def test_urdf_to_constraints_one_per_joint_limit() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    cs = urdf_to_constraints(doc)
    # 6 active joints × 3 limits (position + velocity + effort) = 18
    ids = {c.id for c in cs}
    assert len(ids) == 18
    sp_pos = next(c for c in cs if c.id == "constraint_ur5_shoulder_pan_joint_position")
    assert sp_pos.constraint_type == "safety"
    assert "shoulder_pan_joint" in sp_pos.description
    assert any("joint_limit_violation::joint::shoulder_pan_joint" in s
                for s in sp_pos.violation_signals)


def test_urdf_to_constraints_override_uses_yaml_value() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    cfg = parse_controller_config(FIX / "controller_config.yaml")
    cs = urdf_to_constraints(doc, controller=cfg)
    sp_vel = next(c for c in cs if c.id == "constraint_ur5_shoulder_pan_joint_velocity")
    # YAML override (2.5) wins over URDF (3.15)
    assert "2.500" in sp_vel.description or "2.5" in sp_vel.description


def test_urdf_to_constraints_skips_fixed_joints() -> None:
    doc = parse_urdf(FIX / "ur5.urdf")
    cs = urdf_to_constraints(doc)
    assert all("ft_sensor_joint" not in c.id for c in cs)


def test_constraint_pattern_validates() -> None:
    """All emitted ConstraintPattern objects must clear pydantic validation."""
    doc = parse_urdf(FIX / "ur5.urdf")
    cs = urdf_to_constraints(doc)
    for c in cs:
        assert isinstance(c, ConstraintPattern)
        assert c.id.startswith("constraint_ur5_")
