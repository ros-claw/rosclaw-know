"""Schema tests for TaskCard v1."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rosclaw_know.taskcard.schemas import (
    EmbodimentProfile,
    QualityScores,
    SceneProfile,
    Subtask,
    TaskCard,
    TaskGoal,
    TaskMetadata,
)


def _minimal_taskcard() -> TaskCard:
    return TaskCard(
        metadata=TaskMetadata(
            task_id="test_task",
            title="Test Task",
            created_at="2026-06-20T00:00:00Z",
            confidence=0.5,
        ),
        task=TaskGoal(
            natural_language_goal="test",
            normalized_goal="test",
            task_type="manipulation",
            task_family="test_family",
            domain="test_domain",
        ),
        embodiment=EmbodimentProfile(
            robot_id="r1",
            robot_model="test_bot",
            embodiment_type="manipulator",
        ),
        scene=SceneProfile(scene_id="s1", scene_type="lab"),
        subtasks=[
            Subtask(
                id="st1",
                name="Subtask 1",
                phase="perception",
                description="do something",
            ),
        ],
        failure_taxonomy={"schema_version": "failure_taxonomy.v1", "categories": [], "failures": []},
        physical_constraints={
            "schema_version": "physical_constraints.v1",
            "hard_constraints": [],
            "soft_constraints": [],
            "operational_constraints": [],
            "context_constraints": [],
        },
        engineering_priors=[],
        memory_hooks={"queries": [], "writeback": {"enabled": True}},
        how_hooks={"intervention_triggers": [], "expected_strategies": []},
        auto_hooks={"experiment_candidates": [], "prohibited_experiments": []},
        cognitive_wiki={"enabled": False, "query_terms": [], "imported_cards": []},
        evidence_trace=[],
        quality=QualityScores(),
    )


def test_taskcard_schema_accepts_valid_data():
    card = _minimal_taskcard()
    assert card.metadata.task_id == "test_task"
    assert len(card.subtasks) == 1


def test_taskcard_rejects_empty_subtasks():
    card = _minimal_taskcard()
    data = card.model_dump(mode="json")
    data["subtasks"] = []
    with pytest.raises(ValidationError):
        TaskCard.model_validate(data)


def test_taskcard_rejects_unknown_task_type():
    card = _minimal_taskcard()
    data = card.model_dump(mode="json")
    data["task"]["task_type"] = "unknown"
    with pytest.raises(ValidationError):
        TaskCard.model_validate(data)


def test_quality_scores_bounded():
    with pytest.raises(ValidationError):
        QualityScores(subtask_coverage_score=1.5)
