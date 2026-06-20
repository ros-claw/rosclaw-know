"""UR5 kick-ball recipe — intentionally invalid for the UR5 embodiment."""
from __future__ import annotations

from ._types import TaskRecipe

UR5_KICK_BALL_RECIPE = TaskRecipe(
    task_id="ur5_kick_ball",
    title="UR5 踢足球（不支持）",
    natural_language_goal="让 UR5 踢足球",
    normalized_goal="UR5 cannot kick a soccer ball; it is a fixed-base manipulator lacking locomotion and balance capabilities.",
    task_type="physical_skill",
    task_family="invalid_for_embodiment",
    domain="control_locomotion",
    difficulty="critical",
    expected_outcome=[],
    success_criteria=[],
    tags=["invalid", "manipulator", "locomotion_required"],
    embodiment_type="manipulator",
    relevant_body_parts=[],
    required_capabilities=["bipedal_locomotion", "whole_body_balance", "foot_trajectory_control"],
    assumptions=[],
    subtasks=[],
    valid_robots=[],
)
