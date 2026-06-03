"""Sprint 11: RobotEvent-trajectory extractor — discovery path.

Sister test to ``test_sprint11_robot_evidence_loop.py``.  That file
proves Sprint 9 traces flow into Sprint 6's evidence_distill so
**existing** patterns can be promoted/demoted from real-robot data.
This file proves the *discovery* path: a real-robot agent applies an
**unknown** pattern_id, the placebo-controlled signal beats the
threshold, and Sprint 4 (pattern compiler) receives a fresh
:class:`schemas.CandidatePattern` ready for promotion to the v2
catalog.

Plan §Sprint 11 self-improvement gate:

> 至少有 1 个 CandidatePattern 来自真机 trajectory.
"""
from __future__ import annotations

from pathlib import Path

from rosclaw_know.evidence_distill import ADJUSTED_PROMOTE_THRESHOLD
from rosclaw_know.schemas import CandidatePattern
from rosclaw_know.sim_ingest import (
    events_to_evidence_traces,
    extract_candidates_from_evidence_traces,
    read_robot_event_jsonl,
)
from rosclaw_know.sim_ingest.robot_trajectory_extractor import (
    MIN_TRACE_COUNT,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint11"


# ── pure-function contract ────────────────────────────────────────────────


def test_extractor_returns_empty_for_no_traces() -> None:
    assert extract_candidates_from_evidence_traces([]) == []


def test_extractor_drops_patterns_already_in_catalog() -> None:
    """Sprint 11 discovery loop should NOT re-emit known catalog patterns.

    Sprint 6's bridge_reweighter handles those.  Discovery is for
    pattern_ids the offline catalog hasn't seen.
    """
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    known = {"compiled_zero_integral_gain_on_saturation"}
    cands = extract_candidates_from_evidence_traces(traces, known_pattern_ids=known)
    assert cands == []  # All traces target a known pattern.


def test_extractor_emits_candidate_for_novel_pattern() -> None:
    """The discovery fixture has a new pattern that beats placebo."""
    evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    traces = events_to_evidence_traces(evs)
    cands = extract_candidates_from_evidence_traces(traces, known_pattern_ids=set())
    assert len(cands) == 1

    c = cands[0]
    assert isinstance(c, CandidatePattern)
    assert c.id == "candidate_real_robot_novel_torque_feedback_loop"
    assert c.task_family == "robotics_optimization"
    assert c.evidence_count >= MIN_TRACE_COUNT
    assert c.avg_score_delta is not None
    assert c.avg_score_delta > ADJUSTED_PROMOTE_THRESHOLD
    # Schema gate: id must validate against CandidatePattern regex.
    assert c.id.startswith("candidate_")


def test_candidate_includes_source_trajectory_ids() -> None:
    """Every successful candidate must cite its real-robot traces."""
    evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    traces = events_to_evidence_traces(evs)
    cands = extract_candidates_from_evidence_traces(traces)
    assert cands
    c = cands[0]
    # All 6 fixture traces should be cited.
    assert len(c.source_trajectory_ids) == 6
    # Each trace_id is the run_id::event_type::iteration form.
    for tid in c.source_trajectory_ids:
        assert "::" in tid


def test_candidate_mutation_uses_other_kind() -> None:
    """Real-robot traces lack structural diffs → Mutation.kind="other"."""
    evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    traces = events_to_evidence_traces(evs)
    cands = extract_candidates_from_evidence_traces(traces)
    assert cands
    c = cands[0]
    assert len(c.successful_mutations) == 1
    m = c.successful_mutations[0]
    assert m.kind == "other"
    # Description mentions the agent's free-text diff summary.
    assert "wrist torque feedback" in m.description.lower()


def test_extractor_respects_min_trace_count() -> None:
    """One-shot anecdotes do NOT graduate into a candidate."""
    evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    traces = events_to_evidence_traces(evs)
    # Set the floor higher than our fixture has — should produce nothing.
    cands = extract_candidates_from_evidence_traces(
        traces,
        known_pattern_ids=set(),
        min_trace_count=10,
    )
    assert cands == []


def test_extractor_respects_promote_threshold() -> None:
    """Patterns whose adj-uplift is below threshold are dropped."""
    evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    traces = events_to_evidence_traces(evs)
    # Crank the threshold above what the fixture provides — drop all.
    cands = extract_candidates_from_evidence_traces(
        traces,
        known_pattern_ids=set(),
        promote_threshold=0.99,
    )
    assert cands == []


def test_extractor_is_deterministic() -> None:
    evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    traces = events_to_evidence_traces(evs)
    a = extract_candidates_from_evidence_traces(traces)
    b = extract_candidates_from_evidence_traces(traces)
    assert [c.id for c in a] == [c.id for c in b]
    assert [c.evidence_count for c in a] == [c.evidence_count for c in b]


def test_extractor_handles_traces_with_missing_pattern_id() -> None:
    """If pattern_id is None but task_name shows uplift, still emit."""
    from rosclaw_know.schemas import EvidenceTrace
    traces = [
        EvidenceTrace(
            trace_id=f"run_{i}::task::0",
            run_id=f"run_{i}",
            task_name="emergent_skill",
            iteration=0,
            pattern_id=None,
            strategy="CATALYST",
            pre_score=0.3,
            post_score_5=0.6,
            best_delta_5=0.30 if arm == "true" else 0.04,
            objective_direction="maximize",
            arm=arm,  # type: ignore[arg-type]
        )
        for i, arm in enumerate(["true", "true", "true",
                                  "placebo", "placebo", "placebo"])
    ]
    cands = extract_candidates_from_evidence_traces(traces)
    assert len(cands) == 1
    assert cands[0].id == "candidate_real_robot_emergent_skill"


# ── integration with Sprint 11 evidence loop ──────────────────────────────


def test_discovery_pattern_distinct_from_promotion_pattern() -> None:
    """Sprint 11's two halves operate on disjoint pattern sets.

    The promotion loop (Sprint 6 evidence_distill) is for patterns the
    catalog already has.  The discovery loop (this module) is for
    patterns the catalog doesn't.  Real-robot agents should never
    have a single pattern_id flow through both at the same time.
    """
    promotion_evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    discovery_evs = read_robot_event_jsonl(FIX / "discovery_traces.jsonl")
    promo_pids = {e.fields.get("pattern_id") for e in promotion_evs}
    disco_pids = {e.fields.get("pattern_id") for e in discovery_evs}
    assert promo_pids != disco_pids
    assert "compiled_zero_integral_gain_on_saturation" in promo_pids
    assert "novel_torque_feedback_loop" in disco_pids
    # And the catalog wouldn't normally have the novel one.
    assert "novel_torque_feedback_loop" not in promo_pids
