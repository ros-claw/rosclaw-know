"""Pydantic schemas for ROSClaw-Know TaskCard v1 (physical-task compiler)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── shared config ───────────────────────────────────────────────────────────


class _Base(BaseModel):
    """Fail-loud schema base for TaskCard v1."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ── top-level metadata ──────────────────────────────────────────────────────


class TaskMetadata(_Base):
    task_id: str
    title: str
    created_at: str
    created_by: str = "rosclaw-know"
    compiler_version: str = "0.1.0"
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["draft", "published", "invalid"] = "draft"
    tags: list[str] = Field(default_factory=list)


class TaskGoal(_Base):
    natural_language_goal: str
    normalized_goal: str
    task_type: Literal["physical_skill", "inspection", "navigation", "manipulation"]
    task_family: str
    domain: str
    difficulty: Literal["low", "medium", "high", "critical"] = "medium"
    expected_outcome: list[str] = Field(default_factory=list)
    success_criteria: list[dict[str, Any]] = Field(default_factory=list)


class EmbodimentProfile(_Base):
    robot_id: str
    robot_model: str
    embodiment_type: str
    body_profile_ref: str | None = None
    body_yaml_ref: str | None = None
    eurdf_ref: str | None = None
    relevant_body_parts: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    unavailable_capabilities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class SceneObject(_Base):
    id: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class SceneUncertainty(_Base):
    id: str
    description: str
    expected_range: str


class SceneProfile(_Base):
    scene_id: str
    scene_type: str
    scene_ref: str | None = None
    objects: list[SceneObject] = Field(default_factory=list)
    environmental_assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[SceneUncertainty] = Field(default_factory=list)


class Subtask(_Base):
    id: str
    name: str
    phase: str
    description: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    likely_failures: list[str] = Field(default_factory=list)


class EngineeringPrior(_Base):
    id: str
    type: Literal["safety_prior", "control_prior", "validation_prior", "task_prior"]
    description: str
    applies_to: list[str] = Field(default_factory=list)
    source: Literal[
        "curated",
        "cognitive_wiki",
        "rosclaw_policy",
        "memory",
        "llm_inference",
        "schema_default",
    ]
    confidence: float = Field(ge=0.0, le=1.0)


class MemoryQuery(_Base):
    id: str
    query: str
    intent: Literal[
        "retrieve_similar_episode",
        "retrieve_failure_episode",
        "retrieve_skill_failure",
        "retrieve_task_template",
    ]
    top_k: int = Field(default=5, ge=1, le=50)


class MemoryHooks(_Base):
    queries: list[MemoryQuery] = Field(default_factory=list)
    writeback: dict[str, Any] = Field(default_factory=dict)


class HowTrigger(_Base):
    id: str
    when: dict[str, Any]
    query_hint: str
    expected_strategy: Literal["SAFETY", "CATALYST", "ABSTAIN", "FREE_EXPLORATION"] | None = None


class HowHooks(_Base):
    intervention_triggers: list[HowTrigger] = Field(default_factory=list)
    expected_strategies: list[str] = Field(default_factory=list)


class ExperimentCandidate(_Base):
    id: str
    description: str
    variables: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    safety_gate: bool = True


class AutoHooks(_Base):
    experiment_candidates: list[ExperimentCandidate] = Field(default_factory=list)
    prohibited_experiments: list[str] = Field(default_factory=list)


class WikiCardRef(_Base):
    id: str
    source: str = "cognitive_wiki"
    confidence: float = Field(ge=0.0, le=1.0)


class CognitiveWikiSync(_Base):
    enabled: bool = True
    query_terms: list[str] = Field(default_factory=list)
    imported_cards: list[WikiCardRef] = Field(default_factory=list)


class EvidenceItem(_Base):
    id: str
    type: Literal[
        "user_intent",
        "embodiment_profile",
        "body_yaml",
        "eurdf",
        "scene_file",
        "curated_pattern",
        "cognitive_wiki",
        "memory_retrieval",
        "llm_inference",
        "schema_default",
        "rosclaw_policy",
        "engineering_prior",
        "task_prior",
        "manual_override",
    ]
    claim: str
    source: str
    confidence: float = Field(ge=0.0, le=1.0)


class QualityScores(_Base):
    schema_valid: bool = True
    subtask_coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_taxonomy_coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    constraint_coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    compile_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class TaskCard(_Base):
    """Physical-task compiler output."""

    schema_version: Literal["taskcard.v1"] = "taskcard.v1"
    metadata: TaskMetadata
    task: TaskGoal
    embodiment: EmbodimentProfile
    scene: SceneProfile
    subtasks: list[Subtask]
    failure_taxonomy: dict[str, Any]
    physical_constraints: dict[str, Any]
    engineering_priors: list[EngineeringPrior]
    memory_hooks: MemoryHooks
    how_hooks: HowHooks
    auto_hooks: AutoHooks
    cognitive_wiki: CognitiveWikiSync
    evidence_trace: list[EvidenceItem]
    quality: QualityScores

    @field_validator("subtasks")
    @classmethod
    def _subtasks_non_empty(cls, v: list[Subtask]) -> list[Subtask]:
        if not v:
            raise ValueError("subtasks must not be empty")
        return v


__all__ = [
    "TaskCard",
    "TaskMetadata",
    "TaskGoal",
    "EmbodimentProfile",
    "SceneProfile",
    "SceneObject",
    "SceneUncertainty",
    "Subtask",
    "EngineeringPrior",
    "MemoryQuery",
    "MemoryHooks",
    "HowTrigger",
    "HowHooks",
    "ExperimentCandidate",
    "AutoHooks",
    "WikiCardRef",
    "CognitiveWikiSync",
    "EvidenceItem",
    "QualityScores",
]
