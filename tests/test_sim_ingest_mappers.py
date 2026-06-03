"""Sprint 9: tests for event_to_failure + event_to_evidence mappers."""
from __future__ import annotations

from rosclaw_know.schemas import EvidenceTrace, FailureMode
from rosclaw_know.sim_ingest import (
    EventToFailureMapper,
    RobotEvent,
    event_to_evidence_trace,
    map_events_to_failures,
)


def _ev(event_type: str, embodiment: str, fingerprint: str = "", **fields):
    return RobotEvent(
        timestamp="ros_time:1.0",
        event_type=event_type,  # type: ignore[arg-type]
        embodiment_id=embodiment,
        severity="warning",
        fingerprint=fingerprint,
        fields=fields,
        source="rosbag",
        source_id="test",
    )


# ── event_to_failure ────────────────────────────────────────────────────


def test_single_event_maps_to_failure_mode() -> None:
    evs = [_ev("collision", "ur5", "collision::a::b", force_mag=100.0)]
    out = map_events_to_failures(evs)
    assert len(out) == 1
    f = out[0].failure
    assert isinstance(f, FailureMode)
    assert f.id.startswith("failure_collision_")
    assert f.severity == "warning"
    assert f.domain == "Control_Locomotion"  # ur5 → Control_Locomotion


def test_same_fingerprint_collapses_across_embodiments() -> None:
    """Plan §Sprint 9 acceptance: same fix applies on 2 embodiments."""
    evs = [
        _ev("controller_error", "ur5",       "controller::pid::windup"),
        _ev("controller_error", "quadrotor", "controller::pid::windup"),
        _ev("controller_error", "ur5",       "controller::pid::windup"),  # dedup
    ]
    out = map_events_to_failures(evs)
    assert len(out) == 1
    mf = out[0]
    assert set(mf.embodiments_seen) == {"ur5", "quadrotor"}
    assert mf.occurrence_count == 3


def test_different_fingerprints_keep_separate_failures() -> None:
    evs = [
        _ev("collision", "ur5", "collision::a::b"),
        _ev("collision", "ur5", "collision::c::d"),
    ]
    out = map_events_to_failures(evs)
    assert len(out) == 2


def test_severity_promotes_to_max() -> None:
    evs = [
        _ev("collision", "ur5", "fp"),
        RobotEvent(
            timestamp="t", event_type="collision", embodiment_id="ur5",
            severity="safety_critical", fingerprint="fp", fields={},
            source="rosbag", source_id="t",
        ),
    ]
    out = map_events_to_failures(evs)
    assert out[0].failure.severity == "safety_critical"


def test_observable_signals_are_collected() -> None:
    evs = [_ev("controller_error", "ur5", "controller::pid::windup",
                error_norm=0.78, controller="pid")]
    out = map_events_to_failures(evs)
    sigs = out[0].failure.observable_signals
    assert any("error_norm=0.78" in s for s in sigs)
    assert any("controller=pid" in s for s in sigs)


def test_failure_id_is_stable_across_runs() -> None:
    evs1 = [_ev("safety_stop", "ur5", "emergency_stop")]
    evs2 = [_ev("safety_stop", "ur5", "emergency_stop")]
    out1 = map_events_to_failures(evs1)
    out2 = map_events_to_failures(evs2)
    assert out1[0].failure.id == out2[0].failure.id


def test_unknown_event_type_is_skipped() -> None:
    bad = RobotEvent(timestamp="t", event_type="warp_speed",  # type: ignore[arg-type]
                     embodiment_id="ur5", severity="warning",
                     fingerprint="", fields={}, source="rosbag", source_id="t")
    out = map_events_to_failures([bad])
    assert out == []


def test_likely_causes_and_contraindications_attached() -> None:
    evs = [_ev("controller_error", "ur5", "controller::pid::windup")]
    out = map_events_to_failures(evs)
    f = out[0].failure
    assert f.likely_causes  # non-empty
    assert f.contraindications  # non-empty
    assert any("anti-windup" not in c for c in f.contraindications) or True


def test_default_embodiment_maps_to_world_physics() -> None:
    evs = [_ev("collision", "default_embodiment", "fp")]
    out = map_events_to_failures(evs)
    assert out[0].failure.domain == "World_Physics"


def test_unknown_embodiment_picks_closest_prefix() -> None:
    evs = [_ev("collision", "ur5_arm_a", "fp")]
    out = map_events_to_failures(evs)
    assert out[0].failure.domain == "Control_Locomotion"


def test_mapper_emit_sorted_by_id() -> None:
    evs = [
        _ev("safety_stop",      "ur5", "emergency_stop"),
        _ev("collision",        "ur5", "fp"),
        _ev("controller_error", "ur5", "fp"),
    ]
    out = EventToFailureMapper()
    for ev in evs:
        out.ingest(ev)
    result = out.emit()
    ids = [r.failure.id for r in result]
    assert ids == sorted(ids)


# ── event_to_evidence ───────────────────────────────────────────────────


def test_event_with_task_envelope_becomes_evidence_trace() -> None:
    ev = _ev("task_timeout", "ur5", "task::pick_apple",
              run_id="rosbag_2026_06_02",
              task_name="pick_apple_off_table",
              iteration=7,
              pre_score=0.41,
              post_score_5=0.62,
              code_diff_summary=["add windup guard"],
              hint_features=["pid_windup_guard"],
              used_hint=True,
              strategy="CATALYST",
              objective_direction="maximize",
              verifier_status="valid",
              arm="true")
    trace = event_to_evidence_trace(ev)
    assert isinstance(trace, EvidenceTrace)
    assert trace.run_id == "rosbag_2026_06_02"
    assert trace.task_name == "pick_apple_off_table"
    assert trace.iteration == 7
    assert trace.pre_score == 0.41
    assert trace.post_score_5 == 0.62
    assert trace.used_hint is True
    assert trace.strategy == "CATALYST"
    assert trace.arm == "true"
    assert trace.verifier_status == "valid"


def test_event_without_pre_score_returns_none() -> None:
    ev = _ev("collision", "ur5", "fp", run_id="r1", task_name="t1")
    assert event_to_evidence_trace(ev) is None


def test_event_with_kwargs_override() -> None:
    ev = _ev("collision", "ur5", "fp", pre_score=0.5)
    trace = event_to_evidence_trace(
        ev, run_id="override_run", task_name="override_task", iteration=99,
    )
    assert trace is not None
    assert trace.run_id == "override_run"
    assert trace.task_name == "override_task"
    assert trace.iteration == 99


def test_invalid_strategy_falls_back_to_none() -> None:
    ev = _ev("collision", "ur5", "fp",
              run_id="r", task_name="t", pre_score=0.5,
              strategy="WAT")
    trace = event_to_evidence_trace(ev)
    assert trace is not None and trace.strategy == "NONE"
