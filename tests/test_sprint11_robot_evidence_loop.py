"""Sprint 11: real-robot evidence loop — RobotEvents → EvidenceTrace → distill.

Plan §Sprint 11 (post-v1.5):

> Sprint 9 真机 trace 喂回 Sprint 6 evidence_distill / bridge_reweighter，
> 让 pattern 用得越多越精，离线 OpenEvolve 数据不再是唯一驱动。

These tests pin the end-to-end pipeline:

  RobotEvent JSONL ──read_robot_event_jsonl──> list[RobotEvent]
                                                      │
                                events_to_evidence_traces
                                                      │
                                                      v
                                         list[EvidenceTrace]
                                                      │
                                          evidence_distill.distill
                                                      │
                                                      v
                                  dict[pattern_id, EvidenceStat]
                                                      │
                                                  is_promoted?
                                                      │
                                                      v
                                           bridge_reweighter

The fixture demonstrates a *real-robot promotion*: the v2 catalog's
``compiled_zero_integral_gain_on_saturation`` pattern receives
placebo-controlled evidence from ur5 + quadrotor rollouts and crosses
the §Sprint 6 ``ADJUSTED_PROMOTE_THRESHOLD``.
"""
from __future__ import annotations

from pathlib import Path

from rosclaw_know.evidence_distill import (
    ADJUSTED_PROMOTE_THRESHOLD,
    MIN_SAMPLE_SIZE,
    distill,
    is_demoted,
    is_promoted,
)
from rosclaw_know.sim_ingest import (
    events_to_evidence_traces,
    read_robot_event_jsonl,
)
from rosclaw_know.sim_ingest.event_schema import RobotEvent

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint11"


# ── reader smoke tests ────────────────────────────────────────────────────


def test_reader_parses_all_lines() -> None:
    """10 fixture lines → 10 RobotEvents (no silent drops)."""
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    assert len(evs) == 10
    assert all(isinstance(e, RobotEvent) for e in evs)
    # Two embodiments represented.
    assert {e.embodiment_id for e in evs} == {"ur5", "quadrotor"}


def test_reader_skips_malformed_lines(tmp_path: Path) -> None:
    """A corrupt JSONL line is logged + skipped — never crashes the batch."""
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        '{"timestamp":"t","event_type":"task_timeout","embodiment_id":"ur5",'
        '"fields":{"task_name":"x","run_id":"r","pre_score":0.5}}\n'
        "this is not json\n"
        '{"timestamp":"t2","event_type":"controller_error","embodiment_id":"q",'
        '"fields":{"task_name":"y","run_id":"r2","pre_score":0.6}}\n',
        encoding="utf-8",
    )
    evs = read_robot_event_jsonl(p)
    assert len(evs) == 2
    assert evs[0].event_type == "task_timeout"
    assert evs[1].event_type == "controller_error"


def test_reader_skips_blank_lines(tmp_path: Path) -> None:
    """Empty lines are tolerated."""
    p = tmp_path / "blank.jsonl"
    p.write_text(
        "\n\n"
        '{"timestamp":"t","event_type":"task_timeout","embodiment_id":"ur5",'
        '"fields":{}}\n'
        "\n",
        encoding="utf-8",
    )
    assert len(read_robot_event_jsonl(p)) == 1


# ── events → traces converter ────────────────────────────────────────────


def test_converter_drops_events_without_task_run_envelope() -> None:
    """RobotEvents without task_run fields aren't valid EvidenceTraces."""
    evs = [
        RobotEvent(timestamp="t", event_type="task_timeout", embodiment_id="ur5",
                   severity="info", fingerprint="x", fields={},
                   source="rosbag", source_id="x"),
    ]
    assert events_to_evidence_traces(evs) == []


def test_converter_preserves_event_order() -> None:
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    # 10 events all carry task_run envelopes → 10 traces, order preserved.
    assert [t.run_id for t in traces] == [e.fields["run_id"] for e in evs]


def test_converter_recovers_pattern_id_strategy_arm_pre_score() -> None:
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    pattern_ids = {t.pattern_id for t in traces}
    assert pattern_ids == {"compiled_zero_integral_gain_on_saturation"}
    arms = {t.arm for t in traces}
    assert arms == {"true", "placebo"}
    strategies = {t.strategy for t in traces}
    assert strategies == {"CATALYST"}


# ── end-to-end loop ──────────────────────────────────────────────────────


def test_real_robot_evidence_promotes_pattern() -> None:
    """Plan §Sprint 11 main acceptance.

    A real-robot fixture with ≥5 true + ≥5 placebo traces and
    placebo_adjusted_uplift > 0.03 must trigger ``is_promoted``.
    """
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    stats, coverage = distill(traces)
    assert "compiled_zero_integral_gain_on_saturation" in stats

    stat = stats["compiled_zero_integral_gain_on_saturation"]
    # Sample-size gate met.
    assert stat.n_by_arm["true"] >= MIN_SAMPLE_SIZE
    assert stat.n_by_arm["placebo"] >= MIN_SAMPLE_SIZE
    # Placebo-adjusted uplift positive and above threshold.
    assert stat.placebo_adjusted_uplift is not None
    assert stat.placebo_adjusted_uplift > ADJUSTED_PROMOTE_THRESHOLD
    # Promotion verdict reached from real-robot data alone.
    assert is_promoted(stat)
    assert not is_demoted(stat)


def test_real_robot_evidence_passes_coverage_gates() -> None:
    """No coverage violations on a well-formed real-robot batch.

    The §Sprint 6 coverage gates (injection_id + post_score_3/5 ≥ 80%,
    code_diff_summary ≥ 50%) are what keep us honest — Sprint 11
    fixtures must meet them too.
    """
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    _, coverage = distill(traces)
    assert coverage.violations == []


def test_pattern_originates_from_real_catalog() -> None:
    """The promoted pattern_id is a real v2 catalog entry (compiled_*)."""
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    stats, _ = distill(traces)
    pid = next(iter(stats))
    assert pid.startswith("compiled_"), pid
    # And it's the catalog's anti-windup analogue, as wired in Sprint 10.
    assert pid == "compiled_zero_integral_gain_on_saturation"


def test_no_promotion_when_placebo_matches_true() -> None:
    """Negative control: if placebo arm matches true arm, no promotion."""
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    # Rewrite all placebo traces to have the same best_delta_5 as true.
    rewritten = []
    for t in traces:
        if t.arm == "placebo":
            t = t.model_copy(update={"best_delta_5": 0.28})  # match true
        rewritten.append(t)
    stats, _ = distill(rewritten)
    stat = stats["compiled_zero_integral_gain_on_saturation"]
    # placebo-adjusted uplift should be near zero (true mean - placebo mean).
    assert abs(stat.placebo_adjusted_uplift or 0.0) < 0.05
    assert not is_promoted(stat)


def test_demotion_when_true_underperforms_placebo() -> None:
    """If real-robot true arm UNDERPERFORMS placebo, the pattern demotes."""
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    # Flip the deltas so true is worse than placebo.
    rewritten = []
    for t in traces:
        if t.arm == "true":
            t = t.model_copy(update={"best_delta_5": 0.02})
        elif t.arm == "placebo":
            t = t.model_copy(update={"best_delta_5": 0.30})
        rewritten.append(t)
    stats, _ = distill(rewritten)
    stat = stats["compiled_zero_integral_gain_on_saturation"]
    assert stat.placebo_adjusted_uplift is not None
    assert stat.placebo_adjusted_uplift < 0
    assert is_demoted(stat)
    assert not is_promoted(stat)


# ── Sprint 10 + Sprint 11 close the loop ──────────────────────────────────


def test_sprint_10_table_uses_pattern_promoted_by_sprint_11() -> None:
    """Smoke: the pattern Sprint 11 promotes is exactly the one Sprint 10's
    auto-derived transfer table exposes for controller_error."""
    from rosclaw_know.sim_ingest.cross_embodiment import (
        load_default_transfer_table,
    )
    table = load_default_transfer_table()
    assert "controller_error" in table
    assert "compiled_zero_integral_gain_on_saturation" in table["controller_error"]

    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    stats, _ = distill(traces)
    promoted_ids = {pid for pid, stat in stats.items() if is_promoted(stat)}
    # Same pattern survives both loops:
    #  - Sprint 10's static derivation says it's transferable
    #  - Sprint 11's real-robot evidence says it really works
    assert promoted_ids & set(table["controller_error"])
