"""Evidence-backed retrieval and How advice wire contracts."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .base import StrictContract, ensure_aware
from .source_v2 import EvidenceRefV2


class ReferenceContextV2(StrictContract):
    task: str | None = None
    robot: str | None = None
    simulator: str | None = None
    ros_distro: str | None = None
    software_versions: dict[str, str] = Field(default_factory=dict)
    current_stage: str | None = None
    current_failure: str | None = None


class ReferencePackItemV2(StrictContract):
    rank: int = Field(ge=1)
    project_id: str | None = None
    knowledge_unit_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    why_relevant: str = Field(min_length=1)
    relevance_dimensions: list[str] = Field(default_factory=list)
    mechanism: str = Field(min_length=1)
    what_to_borrow: list[str] = Field(default_factory=list)
    exact_files: list[str] = Field(default_factory=list)
    exact_sections: list[str] = Field(default_factory=list)
    incompatibilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    adaptation_needed: list[str] = Field(default_factory=list)
    source_version: str = Field(min_length=1)
    evidence_refs: list[EvidenceRefV2] = Field(min_length=1)
    score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class ReferenceComparisonV2(StrictContract):
    shared_principles: list[str] = Field(default_factory=list)
    conflicting_assumptions: list[str] = Field(default_factory=list)
    route_tradeoffs: list[str] = Field(default_factory=list)
    preferred_references: list[str] = Field(default_factory=list)


class ReferencePackV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.reference_pack.v2"

    schema_version: Literal["rosclaw.know.reference_pack.v2"] = SCHEMA_VERSION
    reference_pack_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    context: ReferenceContextV2
    generated_at: datetime
    index_version: str = Field(min_length=1)
    items: list[ReferencePackItemV2] = Field(default_factory=list)
    comparison: ReferenceComparisonV2 = Field(default_factory=ReferenceComparisonV2)
    recommended_reading_order: list[str] = Field(default_factory=list)
    suggested_next_checks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    token_budget: int = Field(ge=1)
    truncated: bool = False
    continuation_cursor: str | None = None
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False
    stale: bool = False
    cache_age_seconds: int = Field(default=0, ge=0)

    @field_validator("generated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _unique_ranks(self) -> ReferencePackV2:
        ranks = [item.rank for item in self.items]
        if len(ranks) != len(set(ranks)):
            raise ValueError("item ranks must be unique")
        if self.truncated and not self.continuation_cursor:
            raise ValueError("truncated packs require a continuation_cursor")
        if self.stale and not self.cached:
            raise ValueError("stale Reference Packs must also be marked cached")
        if (self.cached or self.stale) and not self.warnings:
            raise ValueError("cached Reference Packs must explain degradation in warnings")
        return self


class AdviceRecommendationV2(StrictContract):
    action_type: Literal["inspect", "compare", "configure", "implement", "verify", "abstain"]
    description: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRefV2] = Field(default_factory=list)
    safety_class: Literal["advisory"] = "advisory"


class AdviceCandidateDecisionV1(StrictContract):
    knowledge_unit_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    compatibility: Literal[
        "compatible", "partially_compatible", "incompatible", "unknown"
    ]
    accepted: bool
    reasons: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class HowExplanationV1(StrictContract):
    """Public decision evidence; deliberately excludes private reasoning."""

    SCHEMA_VERSION: ClassVar[str] = "rosclaw.how.explanation.v1"

    schema_version: Literal["rosclaw.how.explanation.v1"] = SCHEMA_VERSION
    advice_id: str = Field(min_length=1)
    mode: Literal["discover", "consult", "diagnose", "catalyze"]
    reference_pack_id: str | None = None
    knowledge_used: list[AdviceCandidateDecisionV1] = Field(default_factory=list)
    knowledge_rejected: list[AdviceCandidateDecisionV1] = Field(default_factory=list)
    compatibility_warnings: list[str] = Field(default_factory=list)
    unknown_context: list[str] = Field(default_factory=list)
    recommendation_basis: list[str] = Field(default_factory=list)
    alternative_rejections: list[str] = Field(default_factory=list)
    private_reasoning_disclosed: Literal[False] = False


class HowAdviceBundleV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.how.advice.v2"

    schema_version: Literal["rosclaw.how.advice.v2"] = SCHEMA_VERSION
    advice_id: str = Field(min_length=1)
    mode: Literal["discover", "consult", "diagnose", "catalyze"]
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_pack_id: str | None = None
    reference_pack_cached: bool = False
    reference_pack_stale: bool = False
    reference_pack_age_seconds: int = Field(default=0, ge=0)
    summary: str = Field(min_length=1)
    diagnosis: str | None = None
    recommendations: list[AdviceRecommendationV2] = Field(default_factory=list)
    compatibility_warnings: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    abstained: bool = False
    abstention_reason: str | None = None
    explanation: HowExplanationV1 | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _abstention_is_explained(self) -> HowAdviceBundleV2:
        if self.abstained and not self.abstention_reason:
            raise ValueError("abstained advice requires abstention_reason")
        if self.reference_pack_stale and not self.reference_pack_cached:
            raise ValueError("stale advice must identify a cached Reference Pack")
        return self


class KnowledgeUsageFeedbackV1(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.knowledge_usage_feedback.v1"

    schema_version: Literal["rosclaw.knowledge_usage_feedback.v1"] = SCHEMA_VERSION
    feedback_id: str = Field(min_length=1)
    reference_pack_id: str = Field(min_length=1)
    advice_id: str | None = None
    knowledge_unit_id: str = Field(min_length=1)
    presented: bool = True
    opened: bool = False
    used_by_agent: bool = False
    verdict: Literal["useful", "irrelevant", "stale", "incompatible", "misleading", "unknown"]
    reason: str | None = None
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_ref: str | None = None
    practice_ref: str | None = None
    origin: Literal["user", "agent", "verifier"]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]


class FeedbackGovernanceRecordV1(StrictContract):
    """Auditable, non-mutating consequence of one usage-feedback verdict."""

    SCHEMA_VERSION: ClassVar[str] = "rosclaw.feedback_governance.v1"

    schema_version: Literal["rosclaw.feedback_governance.v1"] = SCHEMA_VERSION
    governance_id: str = Field(min_length=1)
    feedback_id: str = Field(min_length=1)
    reference_pack_id: str = Field(min_length=1)
    knowledge_unit_id: str = Field(min_length=1)
    verdict: Literal["useful", "irrelevant", "stale", "incompatible", "misleading", "unknown"]
    queue: Literal[
        "usage_signals",
        "query_ranking_signals",
        "source_refresh",
        "compatibility_review",
        "ranking_review",
        "manual_review",
    ]
    proposed_action: Literal[
        "record_usage_signal",
        "record_query_family_signal",
        "refresh_source_candidate",
        "compatibility_review_candidate",
        "downweight_review_candidate",
        "manual_review_candidate",
    ]
    status: Literal["signal_recorded", "pending_review", "reviewed", "dismissed"]
    requires_human_review: bool
    automatic_mutation_allowed: Literal[False] = False
    rationale: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created_aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]
