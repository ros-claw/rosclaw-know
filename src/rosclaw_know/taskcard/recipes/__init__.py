"""Deterministic task recipes for the TaskCard v1 compiler."""
from __future__ import annotations

from ._types import SubtaskRecipe, TaskRecipe
from .g1_kick_ball import G1_KICK_BALL_RECIPE
from .g1_press_button import G1_PRESS_BUTTON_RECIPE
from .go2_inspect_meter import GO2_INSPECT_METER_RECIPE
from .ur5_kick_ball import UR5_KICK_BALL_RECIPE
from .ur5_pick_cup import UR5_PICK_CUP_RECIPE

RECIPES: dict[str, TaskRecipe] = {
    "g1_kick_ball": G1_KICK_BALL_RECIPE,
    "g1_press_button": G1_PRESS_BUTTON_RECIPE,
    "go2_inspect_meter": GO2_INSPECT_METER_RECIPE,
    "ur5_pick_cup": UR5_PICK_CUP_RECIPE,
    "ur5_kick_ball": UR5_KICK_BALL_RECIPE,
}

__all__ = [
    "SubtaskRecipe",
    "TaskRecipe",
    "RECIPES",
]
