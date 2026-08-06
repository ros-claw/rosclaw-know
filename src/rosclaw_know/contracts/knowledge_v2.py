"""Project-card and knowledge-unit contracts."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .base import StrictContract, ensure_aware
from .source_v2 import EvidenceRefV2

KnowledgeUnitType = Literal[
    "mechanism",
    "implementation",
    "failure_lesson",
    "compatibility",
    "integration_recipe",
    "design_pattern",
    "project_capability",
]


class ProjectCardV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.project_card.v2"

    schema_version: Literal["rosclaw.know.project_card.v2"] = SCHEMA_VERSION
    project_id: str = Field(min_length=1)
    source_snapshot_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    problem_scope: list[str] = Field(default_factory=list)
    supported_robots: list[str] = Field(default_factory=list)
    supported_simulators: list[str] = Field(default_factory=list)
    ros_distros: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    hardware_requirements: list[str] = Field(default_factory=list)
    training_methods: list[str] = Field(default_factory=list)
    deployment_modes: list[str] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    key_components: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    important_issues: list[str] = Field(default_factory=list)
    important_pull_requests: list[str] = Field(default_factory=list)
    related_papers: list[str] = Field(default_factory=list)
    wiki_root_page: str | None = None
    evidence_refs: list[EvidenceRefV2] = Field(min_length=1)
    provenance_status: Literal["verified", "legacy_unknown", "generated"] = "verified"

    @model_validator(mode="after")
    def _evidence_uses_snapshot(self) -> ProjectCardV2:
        if any(e.snapshot_id != self.source_snapshot_id for e in self.evidence_refs):
            raise ValueError("project evidence must point at source_snapshot_id")
        return self


class KnowledgeVectorsV2(StrictContract):
    problem: list[float] | None = None
    mechanism: list[float] | None = None
    content: list[float] | None = None
    code: list[float] | None = None

    @field_validator("problem", "mechanism", "content", "code")
    @classmethod
    def _nonempty_vector(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and not value:
            raise ValueError("vectors must be non-empty when present")
        return value


class KnowledgeUnitV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.knowledge_unit.v2"

    schema_version: Literal["rosclaw.know.knowledge_unit.v2"] = SCHEMA_VERSION
    knowledge_unit_id: str = Field(min_length=1)
    unit_type: KnowledgeUnitType
    title: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    applicability: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    software_constraints: dict[str, str] = Field(default_factory=dict)
    hardware_constraints: list[str] = Field(default_factory=list)
    robot_constraints: list[str] = Field(default_factory=list)
    source_snapshot_ids: list[str] = Field(min_length=1)
    evidence_refs: list[EvidenceRefV2] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["draft", "verified", "deprecated", "rejected"] = "draft"
    provenance_status: Literal["verified", "legacy_unknown", "generated"] = "verified"
    created_at: datetime
    updated_at: datetime
    vectors: KnowledgeVectorsV2 = Field(default_factory=KnowledgeVectorsV2)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _evidence_is_pinned(self) -> KnowledgeUnitV2:
        snapshot_ids = set(self.source_snapshot_ids)
        if any(e.snapshot_id not in snapshot_ids for e in self.evidence_refs):
            raise ValueError("every evidence ref must use a declared source snapshot")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be >= created_at")
        return self
