"""Shared dataclasses for task recipes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SubtaskRecipe:
    id: str
    name: str
    phase: str
    description: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    likely_failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskRecipe:
    task_id: str
    title: str
    natural_language_goal: str
    normalized_goal: str
    task_type: str
    task_family: str
    domain: str
    difficulty: str
    expected_outcome: list[str]
    success_criteria: list[dict[str, Any]]
    tags: list[str]
    embodiment_type: str
    relevant_body_parts: list[str]
    required_capabilities: list[str]
    assumptions: list[str]
    subtasks: list[SubtaskRecipe]
    required_scene_objects: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    environmental_assumptions: list[str] = field(default_factory=list)
    scene_uncertainty: list[tuple[str, str, str]] = field(default_factory=list)
    additional_failures: list[dict[str, Any]] = field(default_factory=list)
    additional_constraints: list[dict[str, Any]] = field(default_factory=list)
    engineering_priors: list[dict[str, Any]] = field(default_factory=list)
    memory_queries: list[dict[str, Any]] = field(default_factory=list)
    how_triggers: list[dict[str, Any]] = field(default_factory=list)
    auto_experiments: list[dict[str, Any]] = field(default_factory=list)
    prohibited_experiments: list[str] = field(default_factory=list)
    cognitive_wiki_terms: list[str] = field(default_factory=list)
    cognitive_wiki_cards: list[tuple[str, float]] = field(default_factory=list)
    valid_robots: list[str] | None = None


__all__ = ["SubtaskRecipe", "TaskRecipe"]
