"""Compiler tests for TaskCard v1."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rosclaw_know.taskcard import TaskCardCompileError, compile_task
from rosclaw_know.taskcard.compiler import TaskCardCompiler

FIXTURES = Path(__file__).parent / "fixtures"
SCENE = FIXTURES / "scenes" / "lab_soccer.yaml"


@pytest.mark.parametrize(
    "task_id,robot",
    [
        ("g1_kick_ball", "unitree_g1"),
        ("g1_press_button", "unitree_g1"),
        ("go2_inspect_meter", "unitree_go2"),
        ("ur5_pick_cup", "ur5"),
    ],
)
def test_compile_known_tasks(task_id: str, robot: str):
    card = compile_task(task_id, robot=robot, scene_path=str(SCENE), strict=True)
    assert card.metadata.task_id == task_id
    assert card.subtasks
    assert card.failure_taxonomy["failures"]
    assert card.physical_constraints["hard_constraints"]
    assert card.evidence_trace
    assert card.quality.compile_confidence >= 0.5


def test_g1_kick_ball_subtasks_match_gold():
    card = compile_task("g1_kick_ball", robot="unitree_g1", scene_path=str(SCENE))
    ids = [st.id for st in card.subtasks]
    assert ids == ["perceive_ball", "plan_approach", "approach_ball", "align_for_kick", "execute_kick", "recover_balance"]


def test_g1_kick_ball_failure_coverage():
    card = compile_task("g1_kick_ball", robot="unitree_g1", scene_path=str(SCENE))
    failure_ids = {f["id"] for f in card.failure_taxonomy["failures"]}
    expected = {"ball_not_detected", "ball_pose_drift", "unstable_support", "torque_limit_violation", "missed_ball", "fall_risk"}
    assert expected.issubset(failure_ids)


def test_g1_kick_ball_constraint_coverage():
    card = compile_task("g1_kick_ball", robot="unitree_g1", scene_path=str(SCENE))
    all_ids = set()
    for key in ("hard_constraints", "soft_constraints", "operational_constraints", "context_constraints"):
        all_ids.update(item["id"] for item in card.physical_constraints.get(key, []))
    expected = {"no_human_in_kick_direction", "torque_limit", "no_full_power_kick_without_sandbox", "low_speed_first_trial", "maintain_balance_margin", "preserve_recovery_path", "lab_scene_only"}
    assert expected.issubset(all_ids)


def test_ur5_kick_ball_is_invalid():
    with pytest.raises(TaskCardCompileError):
        compile_task("ur5_kick_ball", robot="ur5")


def test_unknown_task_raises():
    with pytest.raises(TaskCardCompileError):
        compile_task("unknown_xyz", robot="unitree_g1")


def test_compile_to_files(tmp_path: Path):
    compiler = TaskCardCompiler()
    paths = compiler.compile_to_files("g1_kick_ball", tmp_path, robot="unitree_g1", scene_path=str(SCENE))
    assert paths["taskcard"].exists()
    assert paths["evidence"].exists()
    assert paths["report"].exists()

    data = yaml.safe_load(paths["taskcard"].read_text(encoding="utf-8"))
    assert data["metadata"]["task_id"] == "g1_kick_ball"


def test_strict_validation_passes():
    card = compile_task("g1_kick_ball", robot="unitree_g1", scene_path=str(SCENE), strict=True)
    assert card.quality.schema_valid
