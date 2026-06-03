"""Sprint 10: data-driven derivation of the cross-embodiment transfer table.

Sprint 9 shipped with a hand-curated ``PATTERN_TRANSFER_TABLE`` mapping
:class:`EventType` to bare pattern-name strings — names like
``"anti_windup"`` that didn't even exist in the v2 catalog.  Sprint 10
replaces that table with a pure function that mines the same mapping
from the FailureMode catalog + FixPattern.failure_ids.

These tests pin the new contract:

1. ``derive_pattern_transfer_table`` is a pure function of (failures,
   fix_patterns).
2. It emits only pattern_ids that exist in the supplied
   ``fix_patterns``.
3. Real catalog patterns like ``compiled_zero_integral_gain_on_saturation``
   come out for the ``controller_error`` event_type because the
   catalog FailureMode ``failure_pid_integrator_windup`` is linked to
   them.
4. The default loader (``load_default_transfer_table``) reads the
   compiled graph at ``data/assets/physical_graph.json`` — no module
   constant required.
5. ``run_cross_embodiment_check`` no longer needs the hand-curated
   ``PATTERN_TRANSFER_TABLE``; it falls back to the auto-derived table
   when ``transfer_table`` is not given.
"""
from __future__ import annotations

from pathlib import Path

from rosclaw_know.schemas import FailureMode, FixPattern
from rosclaw_know.sim_ingest import map_events_to_failures
from rosclaw_know.sim_ingest.cross_embodiment import (
    derive_pattern_transfer_table,
    load_default_transfer_table,
    run_cross_embodiment_check,
)
from rosclaw_know.sim_ingest.event_schema import RobotEvent

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint9"


# ── tiny fixtures ─────────────────────────────────────────────────────────


def _mk_failure(fid: str, symptom: str) -> FailureMode:
    return FailureMode(
        id=fid,
        name=fid,
        domain="Control_Locomotion",
        symptom_text=symptom,
        normalized_symptom=symptom,
    )


def _mk_fix(pid: str, failure_ids: list[str]) -> FixPattern:
    return FixPattern(
        id=pid,
        failure_ids=failure_ids,
        domain="Control_Locomotion",
        fix_summary=f"fix for {failure_ids}",
    )


# ── derive_pattern_transfer_table ────────────────────────────────────────


def test_derive_returns_empty_for_empty_input() -> None:
    """Pure-function contract: no inputs → no entries."""
    assert derive_pattern_transfer_table([], []) == {}


def test_derive_uses_event_type_keyword_to_match_catalog_failure() -> None:
    """controller_error → FailureMode whose id mentions 'controller' or 'windup'."""
    failures = [
        _mk_failure("failure_pid_integrator_windup", "actuator_saturation_with_unbounded_integral"),
    ]
    fixes = [
        _mk_fix("compiled_zero_integral_gain_on_saturation",
                ["failure_pid_integrator_windup"]),
    ]
    table = derive_pattern_transfer_table(failures, fixes)
    assert "controller_error" in table
    assert "compiled_zero_integral_gain_on_saturation" in table["controller_error"]


def test_derive_drops_fix_patterns_with_no_matching_failure() -> None:
    """A FixPattern whose failure_ids point nowhere in failures is dropped."""
    failures = [
        _mk_failure("failure_known", "actuator_saturation_with_unbounded_integral"),
    ]
    fixes = [
        _mk_fix("compiled_known", ["failure_known"]),
        _mk_fix("compiled_orphan", ["failure_unknown"]),
    ]
    table = derive_pattern_transfer_table(failures, fixes)
    flat = {pid for pids in table.values() for pid in pids}
    assert "compiled_known" in flat
    assert "compiled_orphan" not in flat


def test_derive_handles_multi_failure_fix_pattern() -> None:
    """A FixPattern fixing multiple failures shows up for every matching event_type."""
    failures = [
        _mk_failure("failure_controller_divergence_a", "controller_error::a"),
        _mk_failure("failure_actuator_saturation_b", "actuator_saturation::b"),
    ]
    fixes = [
        _mk_fix("compiled_dual",
                ["failure_controller_divergence_a", "failure_actuator_saturation_b"]),
    ]
    table = derive_pattern_transfer_table(failures, fixes)
    assert "compiled_dual" in table.get("controller_error", ())
    assert "compiled_dual" in table.get("actuator_saturation", ())


def test_derive_emits_no_phantom_names() -> None:
    """Bare names like 'anti_windup' (the Sprint-9 typo) must never appear."""
    failures = [
        _mk_failure("failure_pid_integrator_windup",
                    "actuator_saturation_with_unbounded_integral"),
        _mk_failure("failure_actuator_clamp_missing", "missing_command_clamp"),
    ]
    fixes = [
        _mk_fix("compiled_zero_integral_gain_on_saturation",
                ["failure_pid_integrator_windup"]),
        _mk_fix("compiled_controller_output_clamp",
                ["failure_actuator_clamp_missing"]),
    ]
    table = derive_pattern_transfer_table(failures, fixes)
    flat = {pid for pids in table.values() for pid in pids}
    # All derived pids must come from the supplied FixPattern.id values.
    supplied = {fp.id for fp in fixes}
    assert flat <= supplied


def test_derive_returns_sorted_tuples_for_determinism() -> None:
    """Same input → same output (no set-iteration leakage)."""
    failures = [
        _mk_failure("failure_a", "controller_error::x"),
        _mk_failure("failure_b", "controller_error::y"),
    ]
    fixes = [
        _mk_fix("compiled_z", ["failure_a", "failure_b"]),
        _mk_fix("compiled_a", ["failure_a"]),
        _mk_fix("compiled_m", ["failure_b"]),
    ]
    t1 = derive_pattern_transfer_table(failures, fixes)
    t2 = derive_pattern_transfer_table(failures, fixes)
    assert t1 == t2
    # And alphabetically sorted within each bucket.
    for pids in t1.values():
        assert list(pids) == sorted(pids)


# ── load_default_transfer_table ──────────────────────────────────────────


def test_default_transfer_table_loads_from_compiled_graph() -> None:
    """Sprint 10: the default loader reads physical_graph.json."""
    table = load_default_transfer_table()
    # Smoke: at least one event_type → at least one compiled_* pattern.
    assert table
    flat = {pid for pids in table.values() for pid in pids}
    assert any(pid.startswith("compiled_") for pid in flat)


def test_default_transfer_table_contains_zero_integral_for_controller_error() -> None:
    """The catalog has failure_pid_integrator_windup → compiled_zero_integral_gain_on_saturation."""
    table = load_default_transfer_table()
    assert "controller_error" in table
    assert "compiled_zero_integral_gain_on_saturation" in table["controller_error"]


def test_default_transfer_table_has_no_bare_anti_windup_phantom() -> None:
    """Sprint 9 typo: bare name 'anti_windup' must be gone from the live table."""
    table = load_default_transfer_table()
    flat = {pid for pids in table.values() for pid in pids}
    assert "anti_windup" not in flat
    assert "controller_output_clamp" not in flat
    # All emitted ids must be prefixed (compiled_ or candidate_) — i.e. real catalog ids.
    for pid in flat:
        assert pid.startswith(("compiled_", "candidate_")), pid


# ── run_cross_embodiment_check now uses auto-derived table ───────────────


def test_check_works_without_module_constant() -> None:
    """run_cross_embodiment_check must not depend on PATTERN_TRANSFER_TABLE."""
    evs = [
        RobotEvent(timestamp="t1", event_type="controller_error",
                   embodiment_id="ur5", severity="warning",
                   fingerprint="controller::pid::windup",
                   fields={}, source="rosbag", source_id="x"),
        RobotEvent(timestamp="t2", event_type="controller_error",
                   embodiment_id="quadrotor", severity="warning",
                   fingerprint="controller::pid::windup",
                   fields={}, source="rosbag", source_id="x"),
    ]
    failures = map_events_to_failures(evs)
    report = run_cross_embodiment_check(failures)
    assert report.acceptance_pattern_reuse_passed


def test_check_uses_real_compiled_pattern_id_for_anti_windup() -> None:
    """The cross-embodiment row for controller_error must reference a real catalog id."""
    evs = [
        RobotEvent(timestamp="t1", event_type="controller_error",
                   embodiment_id="ur5", severity="warning",
                   fingerprint="controller::pid::windup",
                   fields={}, source="rosbag", source_id="x"),
        RobotEvent(timestamp="t2", event_type="controller_error",
                   embodiment_id="quadrotor", severity="warning",
                   fingerprint="controller::pid::windup",
                   fields={}, source="rosbag", source_id="x"),
    ]
    failures = map_events_to_failures(evs)
    report = run_cross_embodiment_check(failures)
    pids = {r.pattern_id for r in report.patterns_seen_on_multiple_embodiments}
    # At least one real compiled_* id should appear.
    assert any(pid.startswith("compiled_") for pid in pids), pids
    # And the specific anti-windup-like pattern must be there.
    assert "compiled_zero_integral_gain_on_saturation" in pids


def test_explicit_transfer_table_overrides_default() -> None:
    """Callers can still inject a custom table (used by tests / what-if)."""
    evs = [
        RobotEvent(timestamp="t1", event_type="controller_error",
                   embodiment_id="ur5", severity="warning",
                   fingerprint="x", fields={}, source="rosbag", source_id="x"),
        RobotEvent(timestamp="t2", event_type="controller_error",
                   embodiment_id="quadrotor", severity="warning",
                   fingerprint="x", fields={}, source="rosbag", source_id="x"),
    ]
    failures = map_events_to_failures(evs)
    custom = {"controller_error": ("custom_pattern",)}
    report = run_cross_embodiment_check(failures, transfer_table=custom)
    pids = {r.pattern_id for r in report.all_pattern_rows}
    assert pids == {"custom_pattern"}


def test_explicit_empty_table_falls_back_to_no_pattern_rows() -> None:
    """Passing an empty mapping produces zero pattern rows but still computes failures."""
    evs = [
        RobotEvent(timestamp="t1", event_type="controller_error",
                   embodiment_id="ur5", severity="warning",
                   fingerprint="x", fields={}, source="rosbag", source_id="x"),
        RobotEvent(timestamp="t2", event_type="controller_error",
                   embodiment_id="quadrotor", severity="warning",
                   fingerprint="x", fields={}, source="rosbag", source_id="x"),
    ]
    failures = map_events_to_failures(evs)
    report = run_cross_embodiment_check(failures, transfer_table={})
    assert not report.all_pattern_rows
    # Failure-level gate still passes — that's structural and table-independent.
    assert report.acceptance_failure_reuse_passed


# ── catalog drift detection ──────────────────────────────────────────────


def test_default_transfer_table_only_references_known_failure_ids() -> None:
    """Every emitted pattern_id must correspond to a real graph FixPattern."""
    import json

    from rosclaw_know import config as _cfg
    graph = json.loads((_cfg.ASSETS_DIR / "physical_graph.json").read_text())
    fix_ids = {n["id"] for n in graph["nodes"] if n.get("node_type") == "FixPattern"}
    table = load_default_transfer_table()
    flat = {pid for pids in table.values() for pid in pids}
    assert flat <= fix_ids


def test_event_type_universe_covers_canonical_types() -> None:
    """The auto table should populate ≥3 of the 8 EVENT_TYPES.

    Coverage is bounded by how many catalog FailureModes (a) match an
    event_type and (b) have at least one FixPattern targeting them in
    the compiled graph.  Three is the empirical floor for the current
    v1.5 catalog; lower means a regression in the matcher (not the
    catalog).
    """
    table = load_default_transfer_table()
    canonical = {
        "collision", "safety_stop", "joint_limit_violation",
        "controller_error", "sensor_outlier", "task_timeout",
        "trajectory_deviation", "actuator_saturation",
    }
    covered = set(table.keys()) & canonical
    assert len(covered) >= 3, (
        f"only {len(covered)} of 8 canonical event_types covered: {covered}"
    )
    # And the three known-strong buckets must all be there.
    assert {"actuator_saturation", "task_timeout", "controller_error"} <= covered


# ── module surface: bare PATTERN_TRANSFER_TABLE removed ──────────────────


def test_module_no_longer_exports_hand_curated_constant() -> None:
    """The hand-curated dict must be gone from the public surface."""
    import rosclaw_know.sim_ingest.cross_embodiment as ce
    assert not hasattr(ce, "PATTERN_TRANSFER_TABLE"), (
        "Sprint 10 removed PATTERN_TRANSFER_TABLE — use derive_pattern_transfer_table"
    )
