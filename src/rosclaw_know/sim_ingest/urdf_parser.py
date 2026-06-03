"""Sprint 9: URDF + controller config → :class:`EmbodimentCard` + :class:`ConstraintPattern`.

A URDF / e-URDF / SRDF document encodes the *static* truth about a
robot — joint limits, geometry, kinematics, sensors.  Once you have
those you can:

* spawn an :class:`schemas.EmbodimentCard` automatically (the
  ``actuators`` list comes from joint names, ``sensors`` from
  ``<gazebo><sensor>`` blocks, ``control_interfaces`` from
  ``<transmission>`` blocks);
* spawn one :class:`schemas.ConstraintPattern` per joint limit so the
  hybrid retriever can demote any FixPattern that proposes exceeding
  those limits.

The companion ``controller_config.yaml`` (ros2_control style) maps
joints to controllers (position / velocity / effort).  When present,
it lets us populate ``control_interfaces`` more precisely than the
URDF alone.

Why no rospkg / urdf_parser_py dependency:
  * Sprint 9 needs to run in CI without ROS;
  * URDF is just XML, and the subset we care about is < 200 LOC of
    ElementTree walks.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from ..schemas import ConstraintPattern, EmbodimentCard, EmbodimentType

log = logging.getLogger("rosclaw_know.sim_ingest.urdf_parser")


# ── Parsed-URDF dataclasses ─────────────────────────────────────────────


@dataclass(frozen=True)
class URDFJoint:
    """One joint extracted from a URDF document."""

    name: str
    joint_type: str
    parent: str = ""
    child: str = ""
    lower: float | None = None
    upper: float | None = None
    effort: float | None = None
    velocity: float | None = None


@dataclass(frozen=True)
class URDFDoc:
    """Lightweight typed view of a URDF document."""

    robot_name: str
    joints: tuple[URDFJoint, ...]
    sensors: tuple[str, ...]
    transmissions: tuple[str, ...]


@dataclass(frozen=True)
class ControllerConfig:
    """ros2_control / ros_control yaml view."""

    joint_to_controller: dict[str, str] = field(default_factory=dict)
    joint_limit_overrides: dict[str, dict[str, float]] = field(default_factory=dict)


# ── URDF parser ─────────────────────────────────────────────────────────


def _opt_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_urdf(path: str | Path) -> URDFDoc:
    """Parse a URDF / e-URDF file into a :class:`URDFDoc`.

    Skips inertial, collision, visual blocks (they are not needed for
    knowledge ingest).  Tolerates :ref:`xacro`-expanded files as long
    as they have already been resolved to plain XML.
    """
    p = Path(path)
    tree = ET.parse(p)
    root = tree.getroot()
    robot_name = root.attrib.get("name", p.stem)

    joints: list[URDFJoint] = []
    for j in root.findall("joint"):
        name = j.attrib.get("name", "")
        if not name:
            continue
        joint_type = j.attrib.get("type", "fixed")
        parent = (j.find("parent") or ET.Element("p")).attrib.get("link", "")
        child = (j.find("child") or ET.Element("c")).attrib.get("link", "")
        limit_el = j.find("limit")
        lower = upper = effort = velocity = None
        if limit_el is not None:
            lower = _opt_float(limit_el.attrib.get("lower"))
            upper = _opt_float(limit_el.attrib.get("upper"))
            effort = _opt_float(limit_el.attrib.get("effort"))
            velocity = _opt_float(limit_el.attrib.get("velocity"))
        joints.append(URDFJoint(
            name=name, joint_type=joint_type,
            parent=parent, child=child,
            lower=lower, upper=upper,
            effort=effort, velocity=velocity,
        ))

    # Sensors live under <gazebo>/<sensor name="…" type="…"/> in ROS-style
    # URDFs.  Pull every named sensor regardless of nesting depth.
    sensors: list[str] = []
    for s in root.iter("sensor"):
        nm = s.attrib.get("name") or s.attrib.get("type") or ""
        if nm and nm not in sensors:
            sensors.append(nm)

    # Transmissions describe joint↔hardware interface mapping.
    transmissions: list[str] = []
    for t in root.iter("transmission"):
        nm = t.attrib.get("name")
        if nm and nm not in transmissions:
            transmissions.append(nm)

    return URDFDoc(
        robot_name=robot_name,
        joints=tuple(joints),
        sensors=tuple(sensors),
        transmissions=tuple(transmissions),
    )


# ── controller config parser ────────────────────────────────────────────


def parse_controller_config(path: str | Path) -> ControllerConfig:
    """Parse a ros2_control / ros_control yaml file.

    The supported shape (a superset of the canonical
    ros2_control_node config) is::

        controller_manager:
          ros__parameters:
            update_rate: 100
            joint_trajectory_controller:
              type: joint_trajectory_controller/JointTrajectoryController
              joints: [shoulder_pan, elbow]

        joint_limits:
          shoulder_pan:
            max_position: 3.14
            max_velocity: 2.0
            max_effort: 50.0
    """
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    joint_to_controller: dict[str, str] = {}
    cm = ((doc or {}).get("controller_manager") or {}).get("ros__parameters") or {}
    for ctrl_name, ctrl_body in cm.items():
        if not isinstance(ctrl_body, dict):
            continue
        joints = ctrl_body.get("joints")
        if not isinstance(joints, list):
            continue
        for jn in joints:
            joint_to_controller[str(jn)] = str(ctrl_name)
    joint_limits = doc.get("joint_limits") or {}
    overrides: dict[str, dict[str, float]] = {}
    if isinstance(joint_limits, dict):
        for jn, body in joint_limits.items():
            if not isinstance(body, dict):
                continue
            row: dict[str, float] = {}
            for k in ("max_position", "max_velocity", "max_effort", "max_acceleration"):
                v = body.get(k)
                if isinstance(v, (int, float)):
                    row[k] = float(v)
            if row:
                overrides[str(jn)] = row
    return ControllerConfig(
        joint_to_controller=joint_to_controller,
        joint_limit_overrides=overrides,
    )


# ── URDF → EmbodimentCard ───────────────────────────────────────────────


# Heuristic: infer EmbodimentType from joint inventory.
def _embodiment_type_for(doc: URDFDoc) -> EmbodimentType:
    nm = doc.robot_name.lower()
    if any(tok in nm for tok in ("ur5", "kuka", "xarm", "arm")):
        return "manipulator"
    if any(tok in nm for tok in ("go2", "spot", "quadruped", "anymal")):
        return "quadruped"
    if any(tok in nm for tok in ("g1", "h1", "humanoid", "atlas")):
        return "humanoid"
    if any(tok in nm for tok in ("turtlebot", "wheel", "kiwi")):
        return "wheeled_robot"
    if any(tok in nm for tok in ("quadrotor", "drone", "uav")):
        return "uav"
    # If the joint count is < 6 and parent-link mesh is "base_link" we
    # fall back to wheeled; otherwise manipulator is the safest catch-all.
    revolute = [j for j in doc.joints if j.joint_type in ("revolute", "continuous")]
    if len(revolute) >= 5:
        return "manipulator"
    return "wheeled_robot"


def urdf_to_embodiment(
    doc: URDFDoc,
    *,
    embodiment_id: str | None = None,
    embodiment_type: EmbodimentType | None = None,
    controller: ControllerConfig | None = None,
    simulators: Iterable[str] = ("isaac_sim", "mujoco", "gazebo"),
) -> EmbodimentCard:
    """Project a parsed URDF into an :class:`schemas.EmbodimentCard`."""
    et: EmbodimentType = embodiment_type or _embodiment_type_for(doc)
    actuators = sorted({j.name for j in doc.joints if j.joint_type not in ("fixed",)})
    if controller is not None:
        interfaces = sorted({c for c in controller.joint_to_controller.values()})
    else:
        interfaces = list(doc.transmissions)
    # Safety constraints surface as readable strings, separate from the
    # typed ConstraintPattern objects.  Useful in retriever boost text.
    safety_constraints: list[str] = []
    for j in doc.joints:
        if j.lower is not None and j.upper is not None:
            safety_constraints.append(
                f"{j.name}.position ∈ [{j.lower:.3f}, {j.upper:.3f}]"
            )
        if j.effort is not None:
            safety_constraints.append(f"{j.name}.effort ≤ {j.effort:.3f}")
        if j.velocity is not None:
            safety_constraints.append(f"{j.name}.velocity ≤ {j.velocity:.3f}")
    return EmbodimentCard(
        id=embodiment_id or doc.robot_name,
        embodiment_type=et,
        sensors=sorted(set(doc.sensors)),
        actuators=actuators,
        control_interfaces=interfaces,
        common_failures=[],  # populated by event_to_failure pipeline
        simulators=list(simulators),
        safety_constraints=safety_constraints[:64],  # cap for readability
    )


# ── URDF → ConstraintPattern[] ──────────────────────────────────────────


def _sanitize(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in s.lower()).strip("_")


def urdf_to_constraints(
    doc: URDFDoc,
    *,
    embodiment_id: str | None = None,
    controller: ControllerConfig | None = None,
) -> list[ConstraintPattern]:
    """Emit one :class:`ConstraintPattern` per joint+limit triple.

    For an n-DOF arm with full (position, velocity, effort) limits
    this yields up to ``3·n`` constraint patterns.  Each one carries a
    ``check_method`` string that the verifier can run on a code-diff
    sketch ("does the proposed code keep joint X inside its limit?").
    """
    emb_id = embodiment_id or doc.robot_name
    out: list[ConstraintPattern] = []
    for j in doc.joints:
        if j.joint_type in ("fixed",):
            continue
        override = (controller.joint_limit_overrides or {}).get(j.name, {}) if controller else {}

        pos_lo = j.lower
        pos_hi = j.upper
        max_pos_override = override.get("max_position")
        if max_pos_override is not None:
            # Override interpreted as +/- max_position when URDF lower/upper missing.
            pos_lo = pos_lo if pos_lo is not None else -float(max_pos_override)
            pos_hi = pos_hi if pos_hi is not None else float(max_pos_override)

        if pos_lo is not None and pos_hi is not None:
            out.append(ConstraintPattern(
                id=f"constraint_{_sanitize(emb_id)}_{_sanitize(j.name)}_position",
                constraint_type="safety",
                description=(
                    f"{emb_id}: joint {j.name} position must remain within "
                    f"[{pos_lo:.3f}, {pos_hi:.3f}]."
                ),
                check_method=(
                    f"abs(q['{j.name}']) ≤ max(|{pos_lo:.3f}|, |{pos_hi:.3f}|) and "
                    f"{pos_lo:.3f} ≤ q['{j.name}'] ≤ {pos_hi:.3f}"
                ),
                violation_signals=[
                    f"joint_limit_violation::joint::{j.name}",
                    f"{j.name}.position out of bounds",
                ],
                repair_strategies=[
                    f"Clamp trajectory in planner to {j.name} limits",
                    "Re-run IK with constrained search space",
                ],
            ))

        velocity_cap = override.get("max_velocity")
        if velocity_cap is None:
            velocity_cap = j.velocity
        if velocity_cap is not None:
            out.append(ConstraintPattern(
                id=f"constraint_{_sanitize(emb_id)}_{_sanitize(j.name)}_velocity",
                constraint_type="safety",
                description=(
                    f"{emb_id}: joint {j.name} velocity must remain ≤ "
                    f"{float(velocity_cap):.3f}."
                ),
                check_method=f"abs(qdot['{j.name}']) ≤ {float(velocity_cap):.3f}",
                violation_signals=[
                    f"actuator_saturation::velocity::{j.name}",
                ],
                repair_strategies=[
                    "Reduce feed-forward velocity command",
                    "Lower trajectory time-scaling",
                ],
            ))

        effort_cap = override.get("max_effort")
        if effort_cap is None:
            effort_cap = j.effort
        if effort_cap is not None:
            out.append(ConstraintPattern(
                id=f"constraint_{_sanitize(emb_id)}_{_sanitize(j.name)}_effort",
                constraint_type="safety",
                description=(
                    f"{emb_id}: joint {j.name} effort/torque must remain ≤ "
                    f"{float(effort_cap):.3f}."
                ),
                check_method=f"abs(tau['{j.name}']) ≤ {float(effort_cap):.3f}",
                violation_signals=[
                    f"actuator_saturation::torque::{j.name}",
                ],
                repair_strategies=[
                    "Tune feedforward + feedback gain split",
                    "Add anti-windup guard to integral term",
                ],
            ))
    return out


__all__ = (
    "URDFJoint",
    "URDFDoc",
    "ControllerConfig",
    "parse_urdf",
    "parse_controller_config",
    "urdf_to_embodiment",
    "urdf_to_constraints",
)
