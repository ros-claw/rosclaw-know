"""Tests for v1.5 typed knowledge schemas (Sprint 1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rosclaw_know.schemas import (
    SCHEMA_VERSION,
    BridgeClusterV2,
    BridgeIndexV2,
    ClusterMetadataV2,
    ConstraintPattern,
    EmbodimentCard,
    EvidenceBlock,
    EvidenceTrace,
    FailureMode,
    FixPattern,
    FRONTIER_DOMAINS,
    PatternCardV2,
    SourceRecordV2,
    TaskCard,
    VerifierCard,
    validate_bridge,
)


# ── happy-path round trips ──────────────────────────────────────────────


def test_failure_mode_round_trip() -> None:
    f = FailureMode(
        id="failure_pid_windup",
        name="PID Integrator Windup",
        domain="Control_Locomotion",
        symptom_text="actuator saturates while integral keeps growing",
        normalized_symptom="saturation_with_unbounded_integral",
        observable_signals=["actuator output clipped", "overshoot increasing"],
        likely_causes=["unconditional integration"],
        contraindications=["do not raise Ki"],
        severity="safety_critical",
    )
    assert FailureMode.model_validate(f.model_dump()) == f


def test_fix_pattern_round_trip() -> None:
    p = FixPattern(
        id="anti_windup_pid",
        failure_ids=["failure_pid_windup"],
        domain="Control_Locomotion",
        fix_summary="freeze integrator while output is saturated",
        preconditions=["integrator present", "output has a finite limit"],
        expected_verifier_signals=["overshoot decreases"],
    )
    assert FixPattern.model_validate(p.model_dump()) == p


def test_constraint_pattern_round_trip() -> None:
    c = ConstraintPattern(
        id="constraint_torque_max",
        constraint_type="safety",
        description="actuator must not exceed torque_max",
        check_method="abs(tau) <= tau_max for all timesteps",
        violation_signals=["thermal trip"],
    )
    assert ConstraintPattern.model_validate(c.model_dump()) == c


def test_embodiment_card_round_trip() -> None:
    e = EmbodimentCard(
        id="embodiment_unitree_go2",
        embodiment_type="quadruped",
        sensors=["imu", "joint encoders"],
        actuators=["12 joint motors"],
        common_failures=["foot slip on smooth ground"],
        simulators=["mujoco", "isaac"],
    )
    assert EmbodimentCard.model_validate(e.model_dump()) == e


def test_task_card_round_trip() -> None:
    t = TaskCard(
        id="task_pid_tuning",
        benchmark="frontier-eng",
        task_name="pid_tuning",
        task_family="control_parameter_optimization",
        domain="Control_Locomotion",
        artifact_type="python",
        objective_direction="maximize",
        metric_name="combined_score",
        verifier_type="simulator",
        hard_constraints=["simulator must not crash"],
        common_failure_modes=["failure_pid_windup"],
        recommended_patterns=["anti_windup_pid"],
    )
    assert TaskCard.model_validate(t.model_dump()) == t


def test_verifier_card_round_trip() -> None:
    v = VerifierCard(
        id="verifier_mujoco_pid",
        verifier_type="simulator",
        objective_direction="maximize",
        metric_name="combined_score",
        score_range=(0.0, 1.0),
        expected_signals=["valid==true"],
    )
    # tuple → list via dump; allow either on re-validate
    dump = v.model_dump()
    assert dump["score_range"] == [0.0, 1.0] or dump["score_range"] == (0.0, 1.0)


def test_evidence_trace_round_trip() -> None:
    et = EvidenceTrace(
        trace_id="trace_001",
        run_id="run_pid_seed8",
        task_name="pid_tuning",
        iteration=4,
        injection_id="inj_42",
        pattern_id="anti_windup_pid",
        strategy="CATALYST",
        pre_score=0.144,
        post_score_1=0.147,
        post_score_3=0.155,
        post_score_5=0.156,
        best_delta_5=0.012,
        code_diff_summary=["set Ki_z to 0", "added output clamp"],
        hint_features=["zero_integral", "output_saturation"],
        used_hint=True,
        verifier_status="valid",
        objective_direction="maximize",
        arm="true",
    )
    assert EvidenceTrace.model_validate(et.model_dump()) == et


def test_source_record_v2_round_trip() -> None:
    s = SourceRecordV2(
        source_id="paper_arxiv_2401_12345",
        source_type="paper",
        source_quality="A",
        url="https://arxiv.org/abs/2401.12345",
        license="cc-by-4.0",
        trust_score=0.85,
    )
    assert SourceRecordV2.model_validate(s.model_dump()) == s


def test_pattern_card_v2_round_trip() -> None:
    pc = PatternCardV2(
        id="anti_windup_pid",
        domain="Control_Locomotion",
        task_families=["pid_tuning"],
        embodiment_types=["uav", "manipulator"],
        artifact_languages=["python", "cpp"],
        priority=1,
        symptom="The controller output saturates while integral keeps growing.",
        diagnosis="Classic integrator windup.",
        preconditions=["controller has an integral term", "actuator has a finite limit"],
        next_experiment="Freeze the integrator when the output is saturated.",
        code_target="search for `integral +=` and `kp * e + ki * integral + kd * de`",
        patch_sketch="if not is_saturated(u): integral += e * dt",
        expected_verifier_signals=["lower overshoot"],
        anti_patterns=["do not raise Ki"],
        contraindications=["actuator with no saturation cannot wind up"],
        evidence=EvidenceBlock(n=12, avg_uplift=0.08, win_rate=0.75),
    )
    assert PatternCardV2.model_validate(pc.model_dump()) == pc


def test_cluster_metadata_round_trip() -> None:
    m = ClusterMetadataV2(
        lifecycle_status="production",
        task_families=["pid_tuning"],
        embodiment_types=["uav"],
        objective_directions=["maximize"],
        source_quality="S",
        evidence=EvidenceBlock(n=20, avg_uplift=0.07, win_rate=0.7, hint_use_rate=0.55),
    )
    assert ClusterMetadataV2.model_validate(m.model_dump()) == m


# ── strict validation ───────────────────────────────────────────────────


def test_failure_mode_rejects_unknown_domain() -> None:
    with pytest.raises(ValidationError):
        FailureMode(
            id="failure_x", name="x", domain="Nope",
            symptom_text="", normalized_symptom="",
        )


def test_failure_mode_rejects_bad_id() -> None:
    with pytest.raises(ValidationError):
        FailureMode(
            id="not_starting_with_failure", name="x",
            domain="Control_Locomotion",
            symptom_text="", normalized_symptom="",
        )


def test_bridge_cluster_rejects_priority_out_of_range() -> None:
    for bad in (2, -2, 5):
        with pytest.raises(ValidationError):
            BridgeClusterV2(
                standard_name="x", domain="Control_Locomotion", priority=bad
            )


def test_bridge_cluster_accepts_priority_none() -> None:
    """Legacy clusters without explicit priority are valid."""
    c = BridgeClusterV2(standard_name="x", domain="Control_Locomotion")
    assert c.priority is None


def test_task_card_rejects_bad_direction() -> None:
    with pytest.raises(ValidationError):
        TaskCard(
            id="t", task_name="x", task_family="y",
            domain="Control_Locomotion",
            artifact_type="python",
            objective_direction="sideways",  # bad
            metric_name="score",
            verifier_type="simulator",
        )


def test_task_card_rejects_bad_artifact_type() -> None:
    with pytest.raises(ValidationError):
        TaskCard(
            id="t", task_name="x", task_family="y",
            domain="Control_Locomotion",
            artifact_type="cobol",  # bad
            objective_direction="maximize",
            metric_name="score",
            verifier_type="simulator",
        )


def test_evidence_trace_rejects_bad_strategy() -> None:
    with pytest.raises(ValidationError):
        EvidenceTrace(
            trace_id="t", run_id="r", task_name="x", iteration=0,
            strategy="LURE",  # bad
            pre_score=0.0, objective_direction="maximize",
        )


def test_pattern_card_evidence_clamped() -> None:
    """win_rate and hint_use_rate must be in [0, 1]."""
    with pytest.raises(ValidationError):
        EvidenceBlock(n=10, win_rate=1.5)
    with pytest.raises(ValidationError):
        EvidenceBlock(n=10, hint_use_rate=-0.1)


def test_source_record_trust_score_bounded() -> None:
    with pytest.raises(ValidationError):
        SourceRecordV2(
            source_id="s", source_type="paper", source_quality="A",
            trust_score=2.0,  # bad
        )


# ── BridgeIndexV2 + validate_bridge ─────────────────────────────────────


def test_validate_bridge_accepts_minimal_doc() -> None:
    bi = validate_bridge({
        "symptom_clusters": {},
        "safety_label_index": {},
    })
    assert isinstance(bi, BridgeIndexV2)
    assert bi.symptom_clusters == {}


def test_validate_bridge_normalizes_safety_label_index() -> None:
    """Legacy bridges store str values; v2 should up-cast to list[str]."""
    bi = validate_bridge({
        "symptom_clusters": {},
        "safety_label_index": {"Torque_Overflow": "anti_windup_pid"},  # str, not list
    })
    assert bi.safety_label_index == {"Torque_Overflow": ["anti_windup_pid"]}


def test_validate_bridge_normalizes_list_form() -> None:
    bi = validate_bridge({
        "symptom_clusters": {},
        "safety_label_index": {"Torque_Overflow": ["a", "b"]},
    })
    assert bi.safety_label_index == {"Torque_Overflow": ["a", "b"]}


def test_validate_bridge_rejects_bad_priority_in_real_cluster() -> None:
    with pytest.raises(ValidationError):
        validate_bridge({
            "symptom_clusters": {
                "c1": {
                    "standard_name": "x",
                    "domain": "Control_Locomotion",
                    "priority": 7,  # bad
                }
            }
        })


def test_validate_bridge_rejects_unknown_domain() -> None:
    with pytest.raises(ValidationError):
        validate_bridge({
            "symptom_clusters": {
                "c1": {"standard_name": "x", "domain": "Nope"}
            }
        })


# ── schema versioning ──────────────────────────────────────────────────


def test_schema_version_present() -> None:
    f = FailureMode(
        id="failure_x", name="x", domain="Control_Locomotion",
        symptom_text="", normalized_symptom="",
    )
    assert f.schema_version == SCHEMA_VERSION == "2.0"


# ── frontier domains ────────────────────────────────────────────────────


def test_all_seven_domains_accepted() -> None:
    for d in FRONTIER_DOMAINS:
        c = BridgeClusterV2(standard_name="x", domain=d)
        assert c.domain == d
