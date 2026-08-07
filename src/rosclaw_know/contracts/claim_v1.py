"""Temporal, evidence-closed knowledge claims.

Claims are an internal, versioned contract layered below ``KnowledgeUnitV2``.
They deliberately keep truth, utility, compatibility and retrieval signals
separate: usage feedback can never promote a statement into a fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .base import StrictContract, ensure_aware
from .source_v2 import EvidenceRefV2

ClaimType = Literal[
    "source_fact",
    "deterministic_fact",
    "derived_claim",
    "mechanism_explanation",
    "version_change",
    "compatibility_constraint",
    "known_issue",
    "deprecated_api",
    "unsupported_configuration",
    "failed_approach",
    "migration_note",
]
SourceAuthority = Literal["S", "A", "B", "C", "D"]
CompatibilityStatus = Literal[
    "compatible", "partially_compatible", "incompatible", "unknown"
]


class TruthQualityV1(StrictContract):
    """Evidence quality only; no usage or retrieval signal is accepted here."""

    score: float = Field(ge=0.0, le=1.0)
    source_authority: SourceAuthority
    direct_evidence: bool
    corroborating_source_count: int = Field(default=1, ge=0)
    inference: bool = False
    contradiction_resolved: bool = True
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _inference_cannot_claim_perfect_truth(self) -> TruthQualityV1:
        if self.inference and self.score >= 1.0:
            raise ValueError("inferred claims cannot have perfect truth quality")
        return self


class CompatibilityScopeV1(StrictContract):
    robot: str | None = None
    robot_generation: str | None = None
    hardware_arch: str | None = None
    operating_system: str | None = None
    ros_distro: str | None = None
    python: str | None = None
    cuda: str | None = None
    simulator: str | None = None
    library_versions: dict[str, str] = Field(default_factory=dict)
    firmware: str | None = None


class KnowledgeClaimV1(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.knowledge_claim.v1"

    schema_version: Literal["rosclaw.know.knowledge_claim.v1"] = SCHEMA_VERSION
    claim_id: str = Field(min_length=1, max_length=240)
    knowledge_unit_id: str | None = Field(default=None, max_length=240)
    subject: str = Field(min_length=1, max_length=2000)
    predicate: str = Field(min_length=1, max_length=500)
    object: str = Field(min_length=1, max_length=8000)
    claim_type: ClaimType
    source_snapshot_ids: list[str] = Field(min_length=1)
    evidence_refs: list[EvidenceRefV2] = Field(min_length=1)
    truth_quality: TruthQualityV1
    utility_score: float = Field(default=0.5, ge=0.0, le=1.0)
    compatibility_score: float = Field(default=0.5, ge=0.0, le=1.0)
    retrieval_score: float = Field(default=0.0, ge=0.0, le=1.0)
    compatibility_status: CompatibilityStatus = "unknown"
    compatibility_scope: CompatibilityScopeV1 = Field(default_factory=CompatibilityScopeV1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime
    status: Literal["active", "superseded", "contradicted", "rejected"] = "active"
    superseded_by: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    generated_by: str = Field(min_length=1, max_length=500)
    attributed_to: str = Field(min_length=1, max_length=500)
    created_at: datetime
    updated_at: datetime

    @field_validator("valid_from", "valid_to", "observed_at", "created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return ensure_aware(value)

    @model_validator(mode="after")
    def _temporal_and_evidence_closure(self) -> KnowledgeClaimV1:
        snapshots = set(self.source_snapshot_ids)
        if any(evidence.snapshot_id not in snapshots for evidence in self.evidence_refs):
            raise ValueError("every claim evidence ref must use a declared source snapshot")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be >= valid_from")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be >= created_at")
        if self.status == "superseded" and not self.superseded_by:
            raise ValueError("superseded claims must identify a replacement")
        return self

    def is_valid_at(self, value: datetime) -> bool:
        value = ensure_aware(value)  # type: ignore[assignment]
        return bool(
            self.status == "active"
            and (self.valid_from is None or value >= self.valid_from)
            and (self.valid_to is None or value <= self.valid_to)
        )


class SourceDisagreementV1(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.source_disagreement.v1"

    schema_version: Literal["rosclaw.know.source_disagreement.v1"] = SCHEMA_VERSION
    disagreement_id: str = Field(min_length=1, max_length=240)
    subject: str = Field(min_length=1, max_length=2000)
    claim_ids: list[str] = Field(min_length=2)
    source_snapshot_ids: list[str] = Field(min_length=2)
    rationale: str = Field(min_length=1, max_length=8000)
    status: Literal["pending_review", "reviewed", "dismissed"] = "pending_review"
    resolution: str | None = Field(default=None, max_length=8000)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _timestamp_aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _review_has_resolution(self) -> SourceDisagreementV1:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be >= created_at")
        if self.status == "reviewed" and not self.resolution:
            raise ValueError("reviewed disagreements require a resolution")
        return self
