"""Sprint 6: tests for the Evidence Loop V2 distiller (plan §11.8)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from rosclaw_know.evidence_distill import (
    ALL_ARMS,
    MIN_SAMPLE_SIZE,
    ArmStats,
    CoverageReport,
    EvidenceStat,
    distill,
    is_demoted,
    is_promoted,
    stat_to_dict,
    write_stats,
)
from rosclaw_know.schemas import EvidenceTrace

# ── factory ──────────────────────────────────────────────────────────────


def _trace(
    *,
    arm: Literal["baseline", "true", "placebo", "shuffled"] = "true",
    pattern_id: str | None = "compiled_anti_windup",
    pre: float = 0.5,
    post_5: float | None = 0.65,
    post_3: float | None = 0.60,
    post_1: float | None = 0.55,
    used_hint: bool = True,
    code_diff_summary: list[str] | None = None,
    injection_id: str | None = "inj_001",
    strategy: Literal["SAFETY", "FREE_EXPLORATION", "CATALYST", "NONE"] = "CATALYST",
    verifier_status: Literal["valid", "invalid", "crashed", "unknown"] = "valid",
    trace_id: str | None = None,
) -> EvidenceTrace:
    delta = (post_5 - pre) if post_5 is not None else None
    return EvidenceTrace(
        trace_id=trace_id or f"trace_{arm}_{post_5}",
        run_id=f"run_{arm}",
        task_name="PIDTuning",
        iteration=4,
        injection_id=injection_id,
        pattern_id=pattern_id,
        strategy=strategy,
        pre_score=pre,
        post_score_1=post_1,
        post_score_3=post_3,
        post_score_5=post_5,
        best_delta_5=delta,
        code_diff_summary=code_diff_summary or [],
        used_hint=used_hint,
        verifier_status=verifier_status,
        objective_direction="maximize",
        arm=arm,
    )


# ── distill basic ────────────────────────────────────────────────────────


def test_distill_empty_input() -> None:
    stats, cov = distill([])
    assert stats == {}
    assert cov.total == 0
    assert cov.catalyst_total == 0
    assert cov.violations == []


def test_distill_groups_by_pattern_and_arm() -> None:
    traces = [
        _trace(arm="true", trace_id="t1"),
        _trace(arm="placebo", post_5=0.52, trace_id="t2"),
        _trace(arm="true", pattern_id="other_pattern", post_5=0.70, trace_id="t3"),
    ]
    stats, _ = distill(traces)
    assert set(stats) == {"compiled_anti_windup", "other_pattern"}
    awin = stats["compiled_anti_windup"]
    assert awin.n_by_arm["true"] == 1
    assert awin.n_by_arm["placebo"] == 1
    assert awin.n_by_arm["baseline"] == 0


def test_distill_placebo_adjusted_uplift_correct() -> None:
    """Sprint 6 contract: adjusted = mean(true.delta_5) − mean(placebo.delta_5).

    With true=+0.15, placebo=+0.02 → adjusted ≈ +0.13.
    """
    traces = (
        [_trace(arm="true", post_5=0.65, trace_id=f"t_t_{i}") for i in range(5)]
        + [_trace(arm="placebo", post_5=0.52, trace_id=f"t_p_{i}") for i in range(5)]
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    assert stat.placebo_adjusted_uplift is not None
    assert abs(stat.placebo_adjusted_uplift - 0.13) < 1e-3


def test_distill_shuffled_adjusted_uplift() -> None:
    traces = (
        [_trace(arm="true", post_5=0.65, trace_id=f"t_t_{i}") for i in range(5)]
        + [_trace(arm="shuffled", post_5=0.55, trace_id=f"t_s_{i}") for i in range(5)]
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    assert stat.shuffled_adjusted_uplift is not None
    assert abs(stat.shuffled_adjusted_uplift - 0.10) < 1e-3


def test_distill_adjusted_uplift_none_when_arm_empty() -> None:
    """No placebo arm → adjusted=None (refuse to compute against zero)."""
    traces = [_trace(arm="true", trace_id=f"t_{i}") for i in range(5)]
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    assert stat.placebo_adjusted_uplift is None
    assert stat.shuffled_adjusted_uplift is None


def test_distill_hint_use_rate_true_arm_only() -> None:
    """hint_use_rate is the share of TRUE-arm traces with used_hint=True."""
    traces = (
        # True arm: 3 used, 1 not used → 0.75
        [_trace(arm="true", used_hint=True, trace_id=f"t_u_{i}") for i in range(3)]
        + [_trace(arm="true", used_hint=False, trace_id="t_un")]
        # Placebo arm: used_hint=True but shouldn't count (off-arm)
        + [_trace(arm="placebo", used_hint=True, trace_id="t_p")]
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    assert stat.hint_use_rate == 0.75


def test_distill_validity_preservation_rate() -> None:
    traces = (
        [_trace(arm="true", verifier_status="valid", trace_id=f"t_v_{i}") for i in range(3)]
        + [_trace(arm="true", verifier_status="invalid", trace_id="t_iv")]
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    assert stat.by_arm["true"].validity_preservation_rate == 0.75


def test_distill_regression_rate() -> None:
    """regression_rate = fraction with best_delta_5 < 0."""
    traces = (
        [_trace(arm="true", post_5=0.65, trace_id=f"t_w_{i}") for i in range(3)]
        + [_trace(arm="true", post_5=0.4, trace_id="t_r")]  # -0.1 (regression)
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    assert stat.by_arm["true"].regression_rate == 0.25


def test_distill_win_rate() -> None:
    """win_rate threshold = WIN_DELTA_THRESHOLD = 0.05."""
    traces = (
        [_trace(arm="true", post_5=0.65, trace_id=f"t_w_{i}") for i in range(3)]  # +0.15 (win)
        + [_trace(arm="true", post_5=0.52, trace_id=f"t_n_{i}") for i in range(2)]  # +0.02 (not win)
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    # 3/5 wins
    assert stat.by_arm["true"].win_rate == 0.6


def test_distill_raw_uplift_mean_aggregates_all_arms() -> None:
    """raw_uplift_mean covers every arm for backwards compat."""
    traces = (
        [_trace(arm="true", post_5=0.65, trace_id=f"t_t_{i}") for i in range(2)]
        + [_trace(arm="placebo", post_5=0.55, trace_id=f"t_p_{i}") for i in range(2)]
    )
    stats, _ = distill(traces)
    stat = stats["compiled_anti_windup"]
    # 4 deltas: 0.15, 0.15, 0.05, 0.05 → mean = 0.10
    assert abs(stat.raw_uplift_mean - 0.10) < 1e-3


# ── coverage & acceptance ───────────────────────────────────────────────


def test_coverage_gates_pass_when_data_complete() -> None:
    """Plan §Sprint 6: every CATALYST has injection_id, ≥80% have post_3+5, ≥50% have diff."""
    traces = [
        _trace(
            arm="true", trace_id=f"t_{i}",
            code_diff_summary=["added clamp"],
        )
        for i in range(10)
    ]
    _stats, cov = distill(traces)
    assert cov.violations == []


def test_coverage_injection_id_missing_fires() -> None:
    traces = [
        _trace(arm="true", injection_id=None, trace_id="t_no_inj"),
        _trace(arm="true", injection_id="ok", trace_id="t_ok"),
    ]
    _stats, cov = distill(traces)
    assert any("injection_id" in v for v in cov.violations)


def test_coverage_low_post_score_fires() -> None:
    """If <80% of CATALYST traces carry post_score_3, gate fires."""
    traces = [_trace(arm="true", post_3=None, post_5=None, trace_id=f"t_{i}") for i in range(5)]
    _stats, cov = distill(traces)
    assert any("post_score_3" in v for v in cov.violations)
    assert any("post_score_5" in v for v in cov.violations)


def test_coverage_low_diff_fires() -> None:
    """If <50% of CATALYST traces carry a code_diff_summary, gate fires."""
    traces = [
        _trace(arm="true", code_diff_summary=[], trace_id=f"t_{i}")
        for i in range(10)
    ]
    _stats, cov = distill(traces)
    assert any("code_diff_summary" in v for v in cov.violations)


def test_coverage_none_strategy_not_counted_as_catalyst() -> None:
    """A trace with strategy="NONE" should not be counted in CATALYST gates."""
    traces = [
        _trace(arm="baseline", strategy="NONE", injection_id=None, trace_id="b1"),
        _trace(arm="true", trace_id="t1", code_diff_summary=["x"]),
    ]
    _stats, cov = distill(traces)
    assert cov.total == 2
    assert cov.catalyst_total == 1
    assert cov.violations == []


# ── promotion / demotion logic ──────────────────────────────────────────


def _stat_with_adj(adj: float | None, true_n: int = MIN_SAMPLE_SIZE) -> EvidenceStat:
    """Build a minimal EvidenceStat fixture with a forced adjusted_uplift."""
    arm_stub = ArmStats(
        arm="true", n=true_n, avg_uplift_1=0.0, avg_uplift_3=0.0,
        avg_uplift_5=0.0, win_rate=0.0, regression_rate=0.0,
        validity_preservation_rate=0.0,
    )
    return EvidenceStat(
        pattern_id="compiled_test",
        n=true_n,
        n_by_arm={"baseline": 0, "true": true_n, "placebo": true_n, "shuffled": 0},
        by_arm={
            "baseline": ArmStats(arm="baseline", n=0, avg_uplift_1=None, avg_uplift_3=None, avg_uplift_5=None, win_rate=0, regression_rate=0, validity_preservation_rate=0),
            "true": arm_stub,
            "placebo": ArmStats(arm="placebo", n=true_n, avg_uplift_1=0, avg_uplift_3=0, avg_uplift_5=0, win_rate=0, regression_rate=0, validity_preservation_rate=0),
            "shuffled": ArmStats(arm="shuffled", n=0, avg_uplift_1=None, avg_uplift_3=None, avg_uplift_5=None, win_rate=0, regression_rate=0, validity_preservation_rate=0),
        },
        hint_use_rate=0.0,
        placebo_adjusted_uplift=adj,
        shuffled_adjusted_uplift=None,
        raw_uplift_mean=0.0,
        last_seen="",
    )


def test_promote_above_threshold() -> None:
    s = _stat_with_adj(adj=0.05)
    assert is_promoted(s) is True
    assert is_demoted(s) is False


def test_demote_below_threshold() -> None:
    s = _stat_with_adj(adj=-0.05)
    assert is_demoted(s) is True
    assert is_promoted(s) is False


def test_hold_in_between() -> None:
    s = _stat_with_adj(adj=0.01)
    assert is_promoted(s) is False
    assert is_demoted(s) is False


def test_no_promote_below_min_samples() -> None:
    s = _stat_with_adj(adj=0.10, true_n=MIN_SAMPLE_SIZE - 1)
    assert is_promoted(s) is False
    assert is_demoted(s) is False


def test_none_adjusted_means_hold() -> None:
    s = _stat_with_adj(adj=None)
    assert is_promoted(s) is False
    assert is_demoted(s) is False


# ── serialisation ───────────────────────────────────────────────────────


def test_stat_to_dict_round_trip_keys() -> None:
    s = _stat_with_adj(adj=0.10)
    d = stat_to_dict(s)
    for k in (
        "pattern_id", "n", "n_by_arm", "by_arm", "hint_use_rate",
        "placebo_adjusted_uplift", "shuffled_adjusted_uplift",
        "raw_uplift_mean", "last_seen", "is_promoted", "is_demoted",
    ):
        assert k in d


def test_write_stats_creates_valid_json(tmp_path: Path) -> None:
    s = _stat_with_adj(adj=0.10)
    cov = CoverageReport(total=10, catalyst_total=5, catalyst_with_injection_id=5,
                          catalyst_with_post_score_3=5, catalyst_with_post_score_5=5,
                          catalyst_with_code_diff_summary=3)
    out = tmp_path / "evidence_stats.json"
    write_stats({s.pattern_id: s}, cov, out_path=out)
    import json
    payload = json.loads(out.read_text())
    assert payload["schema_version"] == "2.0"
    assert "compiled_test" in payload["patterns"]
    assert payload["coverage"]["catalyst_total"] == 5


# ── integration: distil the seed JSONL ──────────────────────────────────


REPO = Path("/root/workspace/rosclaw/rosclaw_wiki/rosclaw-know")
SEED = REPO / "data" / "exports" / "evidence_traces_seed.jsonl"


@pytest.mark.skipif(not SEED.is_file(), reason="run scripts/seed_evidence_traces.py first")
def test_seed_jsonl_passes_acceptance_gates() -> None:
    """End-to-end: seed traces clear all Sprint-6 gates and yield PROMOTE."""
    from rosclaw_know.evidence_writer import stream_traces

    traces = list(stream_traces(SEED))
    stats, cov = distill(traces)
    assert cov.violations == [], cov.violations
    # Both seed patterns should land in PROMOTE
    for pid, st in stats.items():
        assert is_promoted(st), f"{pid} did not promote (adj={st.placebo_adjusted_uplift})"


# ── ALL_ARMS invariant ──────────────────────────────────────────────────


def test_all_arms_tuple_is_canonical() -> None:
    assert ALL_ARMS == ("baseline", "true", "placebo", "shuffled")
