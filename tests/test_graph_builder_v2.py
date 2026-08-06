"""Sprint 5: tests for the typed Physical Knowledge Graph V2."""
from __future__ import annotations

from pathlib import Path

import pytest

from rosclaw_know.graph_builder_v2 import (
    ALL_RELATIONS,
    GraphBuildReport,
    build_physical_graph,
)
from rosclaw_know.schemas import (
    ConstraintPattern,
    EmbodimentCard,
    EvidenceTrace,
    FailureMode,
    FixPattern,
    TaskCard,
    VerifierCard,
)

# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def fm_windup() -> FailureMode:
    return FailureMode(
        id="failure_windup",
        name="Windup",
        domain="Control_Locomotion",
        symptom_text="actuator saturates while integral accumulates",
        normalized_symptom="windup",
        observable_signals=["output clipped"],
        likely_causes=["unconditional integration"],
        contraindications=["don't raise Ki"],
        severity="safety_critical",
    )


@pytest.fixture
def fm_oom() -> FailureMode:
    return FailureMode(
        id="failure_oom",
        name="Out Of Memory",
        domain="Systems_Compute",
        symptom_text="memory grows unbounded",
        normalized_symptom="oom",
        observable_signals=["OOM"],
        likely_causes=["no eviction"],
        contraindications=[],
        severity="warning",
    )


@pytest.fixture
def fp_anti_windup() -> FixPattern:
    return FixPattern(
        id="fix_anti_windup",
        failure_ids=["failure_windup"],
        domain="Control_Locomotion",
        fix_summary="zero integral gain on saturation",
        preconditions=["controller has integral gain"],
        implementation_steps=["set Ki to zero when saturated", "for pid_tuning"],
        code_targets=["controller.py"],
        expected_verifier_signals=["settling_time_below_threshold"],
        anti_patterns=["raising Ki without changing clamp"],
        source_ids=["t1", "t2"],
    )


@pytest.fixture
def fp_clamp() -> FixPattern:
    return FixPattern(
        id="fix_output_clamp",
        failure_ids=["failure_windup"],
        domain="Control_Locomotion",
        fix_summary="add output clamp",
        preconditions=[],
        implementation_steps=["clip controller output"],
        code_targets=["controller.py"],
        expected_verifier_signals=[],
        anti_patterns=[],
        source_ids=["t3"],
    )


@pytest.fixture
def tc_pid() -> TaskCard:
    return TaskCard(
        id="task_pid_tuning_quadrotor",
        benchmark="frontier-eng",
        task_name="PIDTuning",
        task_family="robotics_optimization",
        domain="Control_Locomotion",
        artifact_type="python",
        objective_direction="maximize",
        metric_name="combined_score",
        hard_constraints=["respect EVOLVE block"],
        verifier_type="benchmark_harness",
        baseline_description="baseline PID",
        common_failure_modes=["failure_windup"],
        recommended_patterns=["anti_windup_pid"],
    )


@pytest.fixture
def tc_pid_arm() -> TaskCard:
    """Sister task of tc_pid in the same family + domain."""
    return TaskCard(
        id="task_pid_tuning_arm",
        benchmark="frontier-eng",
        task_name="ArmPIDTuning",
        task_family="robotics_optimization",
        domain="Control_Locomotion",
        artifact_type="python",
        objective_direction="maximize",
        metric_name="combined_score",
        hard_constraints=[],
        verifier_type="benchmark_harness",
        baseline_description="baseline arm PID",
        common_failure_modes=[],
        recommended_patterns=[],
    )


@pytest.fixture
def ec_quadrotor() -> EmbodimentCard:
    return EmbodimentCard(
        id="embodiment_quadrotor",
        embodiment_type="uav",
        sensors=["imu"],
        actuators=["motor"],
        control_interfaces=["cmd_vel"],
        common_failures=[],
        simulators=[],
        safety_constraints=[],
    )


@pytest.fixture
def vc_harness() -> VerifierCard:
    return VerifierCard(
        id="verifier_harness",
        verifier_type="benchmark_harness",
        objective_direction="maximize",
        metric_name="combined_score",
        score_range=(0.0, 1.0),
        expected_signals=["returncode == 0"],
        validity_checks=["score not NaN"],
        runtime_estimate_seconds=60.0,
    )


# ── plan §11.5 acceptance ────────────────────────────────────────────────


def test_every_fix_pattern_has_failure_edge(
    fm_windup, fp_anti_windup, fp_clamp, tc_pid, ec_quadrotor, vc_harness
) -> None:
    g, report = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup, fp_clamp],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
    )
    assert report.violations == [], report.violations


def test_every_task_card_has_domain_and_verifier(
    fm_windup, fp_anti_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    g, report = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
    )
    # APPLIES_TO → Domain
    applies = [
        (u, v, d)
        for u, v, d in g.out_edges(tc_pid.id, data=True)
        if d.get("relation") == "APPLIES_TO"
    ]
    assert applies, "task lacks APPLIES_TO edge"
    # VALIDATED_BY → VerifierCard
    val = [
        (u, v, d)
        for u, v, d in g.out_edges(tc_pid.id, data=True)
        if d.get("relation") == "VALIDATED_BY"
    ]
    assert val, "task lacks VALIDATED_BY edge"


def test_evidence_trace_must_link_pattern(
    fm_windup, fp_anti_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    """Plan §11.5: every EvidenceTrace connects to a pattern_id."""
    trace = EvidenceTrace(
        trace_id="trace_001",
        run_id="run_001",
        task_name="PIDTuning",
        iteration=1,
        injection_id="inj_001",
        pattern_id=fp_anti_windup.id,
        strategy="CATALYST",
        pre_score=0.5,
        post_score_5=0.7,
        best_delta_5=0.2,
        objective_direction="maximize",
    )
    g, report = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
        traces=[trace],
    )
    assert report.violations == []
    # IMPROVED_BY because best_delta_5 > 0
    rels = [
        d.get("relation")
        for _u, _v, d in g.out_edges(trace.trace_id, data=True)
    ]
    assert "IMPROVED_BY" in rels


def test_evidence_trace_regression_uses_regressed_by(
    fm_windup, fp_anti_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    trace = EvidenceTrace(
        trace_id="trace_neg",
        run_id="run_neg",
        task_name="PIDTuning",
        iteration=1,
        injection_id="inj_neg",
        pattern_id=fp_anti_windup.id,
        strategy="CATALYST",
        pre_score=0.5,
        post_score_5=0.3,
        best_delta_5=-0.2,
        objective_direction="maximize",
    )
    g, _ = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
        traces=[trace],
    )
    rels = [
        d.get("relation")
        for _u, _v, d in g.out_edges(trace.trace_id, data=True)
    ]
    assert "REGRESSED_BY" in rels


def test_evidence_trace_zero_uplift_uses_derived_from(
    fm_windup, fp_anti_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    trace = EvidenceTrace(
        trace_id="trace_zero",
        run_id="run_zero",
        task_name="PIDTuning",
        iteration=1,
        injection_id="inj_zero",
        pattern_id=fp_anti_windup.id,
        strategy="CATALYST",
        pre_score=0.5,
        post_score_5=0.5,
        best_delta_5=0.0,
        objective_direction="maximize",
    )
    g, _ = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
        traces=[trace],
    )
    rels = [
        d.get("relation")
        for _u, _v, d in g.out_edges(trace.trace_id, data=True)
    ]
    assert "DERIVED_FROM" in rels


# ── multi-edge support ───────────────────────────────────────────────────


def test_multidigraph_allows_parallel_relations(
    fm_windup, fp_anti_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    """Same (u, v) pair with two different relations must coexist."""
    constraint = ConstraintPattern(
        id="constraint_torque_max",
        constraint_type="safety",
        description="torque must not exceed peak",
        check_method="post-hoc",
        violation_signals=["torque > torque_max"],
        repair_strategies=["clamp output"],
    )
    fp_with_violation = FixPattern(
        id="fix_with_violation",
        failure_ids=["failure_windup"],
        domain="Control_Locomotion",
        fix_summary="aggressive gain",
        preconditions=[],
        implementation_steps=["raise Kp"],
        code_targets=[],
        expected_verifier_signals=[],
        # The fix violates the constraint AND it's contraindicated for a task
        anti_patterns=["constraint_torque_max", tc_pid.id],
        source_ids=[],
    )
    g, _ = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_with_violation],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
        constraints=[constraint],
    )
    # fix_with_violation should have BOTH a VIOLATES edge to the
    # constraint and a CONTRAINDICATED_FOR edge to the task.
    relations_out = {d["relation"] for _u, _v, d in g.out_edges(fp_with_violation.id, data=True)}
    assert "VIOLATES" in relations_out
    assert "CONTRAINDICATED_FOR" in relations_out
    assert "FIXES" in relations_out


def test_failure_transferable_to_sister_task(
    fm_windup, fp_anti_windup, tc_pid, tc_pid_arm, ec_quadrotor, vc_harness
) -> None:
    """A failure observed on tc_pid should be marked TRANSFERABLE_TO sister tc_pid_arm."""
    g, _ = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup],
        tasks=[tc_pid, tc_pid_arm],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
    )
    rels = [
        (v, d.get("relation"))
        for _u, v, d in g.out_edges(fm_windup.id, data=True)
    ]
    assert (tc_pid_arm.id, "TRANSFERABLE_TO") in rels


def test_fix_to_embodiment_via_family_map(
    fm_windup, fp_anti_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    """task_family_to_embodiment should produce APPLIES_TO from fix → embodiment."""
    fam_map = {"pid_tuning": ["embodiment_quadrotor"]}
    # Adjust the fix so its implementation_steps mention "pid_tuning"
    g, _ = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_anti_windup],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
        task_family_to_embodiment=fam_map,
    )
    # APPLIES_TO edge from fix to embodiment
    rels = [
        (v, d.get("relation"))
        for _u, v, d in g.out_edges(fp_anti_windup.id, data=True)
    ]
    assert (ec_quadrotor.id, "APPLIES_TO") in rels


# ── invariants ───────────────────────────────────────────────────────────


def test_all_relations_literal_has_12_entries() -> None:
    assert len(ALL_RELATIONS) == 12
    # Plan §6.2 lists exactly these — assert order matches doc
    expected = {
        "CAUSES", "FIXES", "VIOLATES", "CONSTRAINED_BY",
        "OBSERVED_IN", "APPLIES_TO", "CONTRAINDICATED_FOR",
        "VALIDATED_BY", "TRANSFERABLE_TO", "DERIVED_FROM",
        "IMPROVED_BY", "REGRESSED_BY",
    }
    assert set(ALL_RELATIONS) == expected


def test_unknown_failure_id_does_not_crash(
    fm_windup, tc_pid, ec_quadrotor, vc_harness
) -> None:
    """A FixPattern referencing an unknown failure_id should still build —
    the edge is kept but the missing failure surfaces as a 'FailureMode_missing'
    node so the operator can audit it."""
    fp_bad = FixPattern(
        id="fix_dangling",
        failure_ids=["failure_does_not_exist"],
        domain="Control_Locomotion",
        fix_summary="...",
    )
    g, report = build_physical_graph(
        failures=[fm_windup],
        fixes=[fp_bad],
        tasks=[tc_pid],
        embodiments=[ec_quadrotor],
        verifiers=[vc_harness],
    )
    # FailureMode_missing pseudo-node should exist with the dangling id
    assert "failure_does_not_exist" in g
    assert g.nodes["failure_does_not_exist"].get("node_type") == "FailureMode_missing"


def test_empty_inputs_produce_only_domain_nodes() -> None:
    g, report = build_physical_graph()
    # 7 frontier domains seeded as Domain pseudo-nodes
    assert report.nodes_by_type.get("Domain", 0) == 7
    assert report.node_count == 7
    assert report.edge_count == 0
    assert report.violations == []


# ── integration: build from real assets ──────────────────────────────────


REPO = Path(__file__).resolve().parents[1]
HAS_ASSETS = (
    (REPO / "data/assets/failure_taxonomy.yaml").is_file()
    and (REPO / "data/assets/task_cards.yaml").is_file()
    and (REPO / "data/assets/trajectory_patterns.yaml").is_file()
    and (REPO / "data/assets/embodiments.yaml").is_file()
    and (REPO / "data/assets/verifier_cards.yaml").is_file()
)


@pytest.mark.skipif(not HAS_ASSETS, reason="full asset set not generated")
def test_build_graph_from_real_assets_passes_acceptance() -> None:
    """End-to-end: load every YAML, compile candidates, build graph,
    expect zero violations."""
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    import build_physical_graph as cli  # noqa: E402

    failures = cli._load_failures(REPO / "data/assets/failure_taxonomy.yaml")
    tasks = cli._load_tasks(REPO / "data/assets/task_cards.yaml")
    cands = cli._load_candidates(REPO / "data/assets/trajectory_patterns.yaml")
    embos = cli._load_embodiments(REPO / "data/assets/embodiments.yaml")
    vers = cli._load_verifiers(REPO / "data/assets/verifier_cards.yaml")

    _cards, fixes = cli.compile_candidates(cands, failures)
    g, report = build_physical_graph(
        failures=failures, fixes=fixes, tasks=tasks,
        embodiments=embos, verifiers=vers,
        task_family_to_embodiment=cli._FAMILY_TO_EMBODIMENT,
    )
    assert report.violations == [], report.violations
    # Sanity: every node-type bucket non-empty
    for t in ("Domain", "FailureMode", "FixPattern", "TaskCard",
              "EmbodimentCard", "VerifierCard"):
        assert report.nodes_by_type.get(t, 0) > 0, f"no {t} nodes"


def test_graph_build_report_dataclass_shape() -> None:
    """GraphBuildReport must be a frozen dataclass with the documented fields."""
    g, report = build_physical_graph()
    assert isinstance(report, GraphBuildReport)
    # frozen → can't mutate
    with pytest.raises((AttributeError, TypeError)):
        report.node_count = 999  # type: ignore[misc]
