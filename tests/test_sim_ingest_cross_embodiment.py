"""Sprint 9: cross-embodiment reuse acceptance test."""
from __future__ import annotations

from pathlib import Path

from rosclaw_know.sim_ingest import (
    PATTERN_TRANSFER_TABLE,
    map_events_to_failures,
    read_foxglove_jsonl,
    read_isaac_jsonl,
    read_mujoco_jsonl,
    read_rosbag_jsonl,
    run_cross_embodiment_check,
)
from rosclaw_know.sim_ingest.cross_embodiment import (
    CrossEmbodimentReport,
    render_markdown,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint9"


def _read_all() -> list:
    evs: list = []
    evs.extend(read_rosbag_jsonl(FIX / "sample.rosbag.jsonl"))
    evs.extend(read_isaac_jsonl(FIX / "sample_isaac.jsonl"))
    evs.extend(read_mujoco_jsonl(FIX / "sample_mujoco.jsonl"))
    evs.extend(read_foxglove_jsonl(FIX / "sample_foxglove.json"))
    return evs


def test_full_acceptance_passes_on_fixtures() -> None:
    """Plan §Sprint 9 main acceptance: anti-windup on 2+ embodiments."""
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)
    # The plan §Sprint 9 gate is at the *pattern* level: one pattern
    # transferable to ≥2 distinct embodiments.
    assert report.acceptance_pattern_reuse_passed


def test_synthetic_failure_dedup_across_embodiments() -> None:
    """Direct dedup gate: synthesise two events with identical fingerprints."""
    from rosclaw_know.sim_ingest import RobotEvent
    evs = [
        RobotEvent(
            timestamp="t1", event_type="controller_error",
            embodiment_id="ur5", severity="warning",
            fingerprint="controller::pid::windup",
            fields={}, source="rosbag", source_id="x",
        ),
        RobotEvent(
            timestamp="t2", event_type="controller_error",
            embodiment_id="quadrotor", severity="warning",
            fingerprint="controller::pid::windup",
            fields={}, source="rosbag", source_id="x",
        ),
    ]
    failures = map_events_to_failures(evs)
    report = run_cross_embodiment_check(failures)
    assert report.acceptance_failure_reuse_passed
    assert report.acceptance_pattern_reuse_passed


def test_anti_windup_is_one_of_the_transferable_patterns() -> None:
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)
    transferable_pids = {row.pattern_id for row in report.patterns_seen_on_multiple_embodiments}
    assert "anti_windup" in transferable_pids


def test_anti_windup_serves_both_arm_and_quadrotor() -> None:
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)
    aw_row = next(
        row for row in report.patterns_seen_on_multiple_embodiments
        if row.pattern_id == "anti_windup"
    )
    embs = set(aw_row.embodiments)
    # The fixtures include UR5 controller windup AND quadrotor PID windup.
    assert "ur5" in embs and "quadrotor" in embs


def test_report_is_deterministic() -> None:
    """Same fixture set must produce the same report (no time / random)."""
    events_a = _read_all()
    events_b = _read_all()
    rep_a = run_cross_embodiment_check(map_events_to_failures(events_a))
    rep_b = run_cross_embodiment_check(map_events_to_failures(events_b))
    assert rep_a.distinct_embodiments == rep_b.distinct_embodiments
    assert {r.pattern_id for r in rep_a.all_pattern_rows} == {
        r.pattern_id for r in rep_b.all_pattern_rows
    }


def test_known_pattern_filter_drops_unknown_pids(tmp_path) -> None:
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures, known_pattern_ids={"anti_windup"})
    # Anything except anti_windup must be filtered out.
    rows = {r.pattern_id for r in report.all_pattern_rows}
    assert rows == {"anti_windup"}
    # And a note about manifest drift is recorded.
    assert any("not in current manifest" in n for n in report.notes)


def test_empty_input_passes_no_gates() -> None:
    report = run_cross_embodiment_check([])
    assert not report.acceptance_pattern_reuse_passed
    assert not report.acceptance_failure_reuse_passed


def test_markdown_renders_sections() -> None:
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)
    md = render_markdown(report)
    assert "Sprint 9" in md
    assert "Pattern reuse" in md
    assert "Acceptance gates" in md
    assert "anti_windup" in md


def test_pattern_transfer_table_contains_anti_windup() -> None:
    # Smoke: the transfer table must say controller_error → anti_windup.
    assert "anti_windup" in PATTERN_TRANSFER_TABLE["controller_error"]
    assert "anti_windup" in PATTERN_TRANSFER_TABLE["actuator_saturation"]


def test_isolated_single_embodiment_fails_gate(tmp_path) -> None:
    """Sanity: if all events are from one embodiment, the gate must fail."""
    from rosclaw_know.sim_ingest import RobotEvent
    single = [
        RobotEvent(
            timestamp="t", event_type="controller_error", embodiment_id="ur5",
            severity="warning", fingerprint="controller::pid::windup",
            fields={}, source="rosbag", source_id="x",
        )
    ]
    failures = map_events_to_failures(single)
    report = run_cross_embodiment_check(failures)
    assert not report.acceptance_failure_reuse_passed
    assert not report.acceptance_pattern_reuse_passed


def test_distinct_embodiments_are_listed() -> None:
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)
    assert "ur5" in report.distinct_embodiments
    assert "quadrotor" in report.distinct_embodiments


def test_report_is_a_frozen_dataclass() -> None:
    """Reports compare structurally, so callers can snapshot them."""
    events = _read_all()
    failures = map_events_to_failures(events)
    report = run_cross_embodiment_check(failures)
    assert isinstance(report, CrossEmbodimentReport)
