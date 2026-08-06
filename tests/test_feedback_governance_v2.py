from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rosclaw_know.contracts import KnowledgeUsageFeedbackV1
from rosclaw_know.feedback_governance import governance_for_feedback
from rosclaw_know.store import InMemoryKnowStore


@pytest.mark.parametrize(
    ("verdict", "queue", "status", "requires_review"),
    [
        ("useful", "usage_signals", "signal_recorded", False),
        ("irrelevant", "query_ranking_signals", "signal_recorded", False),
        ("stale", "source_refresh", "pending_review", True),
        ("incompatible", "compatibility_review", "pending_review", True),
        ("misleading", "ranking_review", "pending_review", True),
        ("unknown", "manual_review", "pending_review", True),
    ],
)
def test_feedback_routes_to_conservative_governance(
    verdict, queue, status, requires_review
):
    feedback = KnowledgeUsageFeedbackV1(
        feedback_id=f"feedback-{verdict}",
        reference_pack_id="pack-1",
        knowledge_unit_id="unit-1",
        verdict=verdict,
        reason="fixture verdict",
        context_hash="a" * 64,
        origin="verifier",
        created_at=datetime.now(UTC),
    )
    record = governance_for_feedback(feedback)
    assert record.queue == queue
    assert record.status == status
    assert record.requires_human_review is requires_review
    assert record.automatic_mutation_allowed is False


def test_store_persists_governance_idempotently_and_filters_queue():
    store = InMemoryKnowStore()
    feedback = KnowledgeUsageFeedbackV1(
        feedback_id="feedback-stale",
        reference_pack_id="pack-1",
        knowledge_unit_id="unit-1",
        verdict="stale",
        context_hash="b" * 64,
        origin="user",
        created_at=datetime.now(UTC),
    )
    assert store.put_feedback(feedback) is True
    assert store.put_feedback(feedback) is False

    records = store.list_feedback_governance(queue="source_refresh")
    assert len(records) == 1
    assert records[0].feedback_id == feedback.feedback_id
    assert store.statistics()["feedback_governance_count"] == 1

    conflicting = feedback.model_copy(update={"verdict": "useful"})
    with pytest.raises(ValueError, match="feedback ID conflict"):
        store.put_feedback(conflicting)
    assert store.list_feedback_governance(queue="usage_signals") == []
