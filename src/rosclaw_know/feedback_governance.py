"""Conservative governance routing for v2 knowledge usage feedback."""

from __future__ import annotations

import hashlib

from rosclaw_know.contracts import FeedbackGovernanceRecordV1, KnowledgeUsageFeedbackV1

_ROUTES = {
    "useful": (
        "usage_signals",
        "record_usage_signal",
        "signal_recorded",
        False,
        "Usage is recorded as a positive signal; it does not promote or rewrite knowledge.",
    ),
    "irrelevant": (
        "query_ranking_signals",
        "record_query_family_signal",
        "signal_recorded",
        False,
        "Irrelevance is a query-family ranking signal; it does not delete the unit.",
    ),
    "stale": (
        "source_refresh",
        "refresh_source_candidate",
        "pending_review",
        True,
        "Staleness schedules a source-refresh candidate; existing evidence remains immutable.",
    ),
    "incompatible": (
        "compatibility_review",
        "compatibility_review_candidate",
        "pending_review",
        True,
        "Incompatibility enters review for constraints or a candidate compatibility unit.",
    ),
    "misleading": (
        "ranking_review",
        "downweight_review_candidate",
        "pending_review",
        True,
        "Misleading advice is a downweight candidate pending review, never an automatic demotion.",
    ),
    "unknown": (
        "manual_review",
        "manual_review_candidate",
        "pending_review",
        True,
        "Unknown feedback requires manual triage and has no automatic ranking effect.",
    ),
}


def governance_for_feedback(feedback: KnowledgeUsageFeedbackV1) -> FeedbackGovernanceRecordV1:
    queue, action, status, review, rationale = _ROUTES[feedback.verdict]
    suffix = hashlib.sha256(feedback.feedback_id.encode()).hexdigest()[:20]
    return FeedbackGovernanceRecordV1(
        governance_id=f"feedback_governance_{suffix}",
        feedback_id=feedback.feedback_id,
        reference_pack_id=feedback.reference_pack_id,
        knowledge_unit_id=feedback.knowledge_unit_id,
        verdict=feedback.verdict,
        queue=queue,
        proposed_action=action,
        status=status,
        requires_human_review=review,
        automatic_mutation_allowed=False,
        rationale=rationale,
        created_at=feedback.created_at,
    )


__all__ = ["governance_for_feedback"]
