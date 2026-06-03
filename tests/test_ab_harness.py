"""Sprint 8: tests for the 6-arm A/B harness (plan §Sprint 8 acceptance)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rosclaw_know.ab_harness import (
    ALL_ARMS,
    AcceptanceGate,
    AcceptanceReport,
    ArmSummary,
    TaskRunResult,
    TaskSpec,
    _aggregate_cells,
    _rank_arms_within_task,
    acceptance_report,
    compute_arm_summaries,
    pairwise_win_rate,
    paired_trend_p_value,
    performance_profile,
    post_injection_deltas,
    render_markdown,
    run_matrix,
    to_jsonable,
)
from rosclaw_know.ab_synthetic import synthetic_run_fn


# ── shorthand ───────────────────────────────────────────────────────────


def _result(
    task_id: str,
    arm: str,
    seed: int,
    score: float | None,
    *,
    direction: str = "maximize",
    valid: bool = True,
    hint_use: float = 0.0,
) -> TaskRunResult:
    return TaskRunResult(
        task_id=task_id, arm=arm, seed=seed,  # type: ignore[arg-type]
        score=score,
        objective_direction=direction,  # type: ignore[arg-type]
        valid=valid,
        hint_use_rate=hint_use,
    )


# ── _aggregate_cells / mean / validity ──────────────────────────────────


def test_aggregate_cells_means_per_arm() -> None:
    results = [
        _result("t1", "baseline", 1, 0.50),
        _result("t1", "baseline", 2, 0.52),
        _result("t1", "true_know", 1, 0.70),
    ]
    cells = _aggregate_cells(results)
    base = cells[("t1", "baseline")]
    true_arm = cells[("t1", "true_know")]
    assert base.n == 2
    assert abs(base.mean_score - 0.51) < 1e-9
    assert true_arm.mean_score == 0.7


def test_aggregate_skips_invalid_in_mean() -> None:
    """Invalid runs contribute to ``n`` and validity-rate but not the mean."""
    results = [
        _result("t1", "baseline", 1, 0.50, valid=True),
        _result("t1", "baseline", 2, None, valid=False),
    ]
    cells = _aggregate_cells(results)
    base = cells[("t1", "baseline")]
    assert base.n == 2
    assert base.n_valid == 1
    assert base.mean_score == 0.50
    assert base.validity_preservation_rate == 0.5


# ── _rank_arms_within_task ──────────────────────────────────────────────


def test_rank_maximize() -> None:
    results = [
        _result("t1", "baseline", 1, 0.4),
        _result("t1", "true_know", 1, 0.7),
        _result("t1", "placebo_know", 1, 0.5),
    ]
    cells = _aggregate_cells(results)
    ranks = _rank_arms_within_task(cells, "t1", ["baseline", "true_know", "placebo_know"])
    assert ranks["true_know"] == 1.0
    assert ranks["placebo_know"] == 2.0
    assert ranks["baseline"] == 3.0


def test_rank_minimize() -> None:
    """For 'minimize', smaller score wins."""
    results = [
        _result("t1", "baseline", 1, 0.4, direction="minimize"),
        _result("t1", "true_know", 1, 0.1, direction="minimize"),
        _result("t1", "placebo_know", 1, 0.3, direction="minimize"),
    ]
    cells = _aggregate_cells(results)
    ranks = _rank_arms_within_task(cells, "t1", ["baseline", "true_know", "placebo_know"])
    assert ranks["true_know"] == 1.0
    assert ranks["placebo_know"] == 2.0
    assert ranks["baseline"] == 3.0


def test_rank_ties_get_fractional_rank() -> None:
    results = [
        _result("t1", "baseline", 1, 0.50),
        _result("t1", "true_know", 1, 0.50),
        _result("t1", "placebo_know", 1, 0.30),
    ]
    cells = _aggregate_cells(results)
    ranks = _rank_arms_within_task(cells, "t1", ["baseline", "true_know", "placebo_know"])
    # Tied first two: avg(1, 2) = 1.5
    assert ranks["baseline"] == 1.5
    assert ranks["true_know"] == 1.5
    assert ranks["placebo_know"] == 3.0


def test_rank_missing_arm_gets_worst() -> None:
    """An arm with no valid runs gets pushed to the post-last position."""
    results = [
        _result("t1", "baseline", 1, 0.4),
        _result("t1", "true_know", 1, None, valid=False),
    ]
    cells = _aggregate_cells(results)
    ranks = _rank_arms_within_task(cells, "t1", ["baseline", "true_know"])
    assert ranks["baseline"] == 1.0
    assert ranks["true_know"] == 2.0  # last + 1 over only 1 scored


# ── compute_arm_summaries ───────────────────────────────────────────────


def test_arm_summary_baseline_delta_zero() -> None:
    results = [
        _result("t1", "baseline", 1, 0.5),
        _result("t1", "true_know", 1, 0.8),
    ]
    summaries = compute_arm_summaries(results, arms=["baseline", "true_know"])
    assert summaries["baseline"].avg_post_injection_delta_vs_baseline == 0.0
    assert summaries["baseline"].win_rate_vs_baseline == 0.0


def test_arm_summary_true_know_positive_delta() -> None:
    results = [
        _result("t1", "baseline", 1, 0.5),
        _result("t1", "true_know", 1, 0.8),
    ]
    summaries = compute_arm_summaries(results, arms=["baseline", "true_know"])
    assert summaries["true_know"].avg_post_injection_delta_vs_baseline > 0
    assert summaries["true_know"].win_rate_vs_baseline == 1.0


def test_arm_summary_minimize_direction() -> None:
    """For 'minimize', a *lower* arm score should give positive delta."""
    results = [
        _result("t1", "baseline", 1, 0.5, direction="minimize"),
        _result("t1", "true_know", 1, 0.2, direction="minimize"),
    ]
    summaries = compute_arm_summaries(results, arms=["baseline", "true_know"])
    assert summaries["true_know"].avg_post_injection_delta_vs_baseline > 0
    assert summaries["true_know"].win_rate_vs_baseline == 1.0


# ── pairwise_win_rate ───────────────────────────────────────────────────


def test_pairwise_win_rate_directional() -> None:
    results = [
        _result("t1", "true_know", 1, 0.7),
        _result("t1", "placebo_know", 1, 0.4),
        _result("t2", "true_know", 1, 0.2, direction="minimize"),
        _result("t2", "placebo_know", 1, 0.6, direction="minimize"),
    ]
    # Both: true_know strictly beats placebo_know → 100%
    assert pairwise_win_rate(results, "true_know", "placebo_know") == 1.0
    # And the reverse: placebo never beats true_know
    assert pairwise_win_rate(results, "placebo_know", "true_know") == 0.0


# ── post_injection_deltas ───────────────────────────────────────────────


def test_post_injection_deltas_none_when_missing() -> None:
    results = [_result("t1", "true_know", 1, 0.8)]  # no baseline
    deltas = post_injection_deltas(results, "true_know")
    assert deltas["t1"] is None


def test_post_injection_deltas_maximize_positive_means_better() -> None:
    results = [
        _result("t1", "baseline", 1, 0.5),
        _result("t1", "true_know", 1, 0.7),
    ]
    deltas = post_injection_deltas(results, "true_know")
    assert deltas["t1"] is not None and deltas["t1"] > 0


def test_post_injection_deltas_minimize_positive_means_better() -> None:
    results = [
        _result("t1", "baseline", 1, 0.5, direction="minimize"),
        _result("t1", "true_know", 1, 0.3, direction="minimize"),
    ]
    deltas = post_injection_deltas(results, "true_know")
    assert deltas["t1"] is not None and deltas["t1"] > 0


# ── paired_trend_p_value ────────────────────────────────────────────────


def test_paired_p_value_zero_when_identical() -> None:
    results = [
        _result("t1", "baseline", s, 0.5) for s in (1, 2)
    ] + [
        _result("t1", "true_know", s, 0.5) for s in (1, 2)
    ]
    p = paired_trend_p_value(results, "true_know")["t1"]
    # Identical samples → no evidence of trend → p≈1
    assert p == 1.0


def test_paired_p_value_strong_separation_is_small() -> None:
    results = [
        _result("t1", "baseline", 1, 0.50),
        _result("t1", "baseline", 2, 0.52),
        _result("t1", "baseline", 3, 0.49),
        _result("t1", "true_know", 1, 0.80),
        _result("t1", "true_know", 2, 0.82),
        _result("t1", "true_know", 3, 0.81),
    ]
    p = paired_trend_p_value(results, "true_know")["t1"]
    assert p is not None and p < 0.01


def test_paired_p_value_none_when_insufficient_data() -> None:
    results = [
        _result("t1", "baseline", 1, 0.5),
        _result("t1", "true_know", 1, 0.8),  # only one sample each
    ]
    p = paired_trend_p_value(results, "true_know")["t1"]
    assert p is None


# ── performance_profile ─────────────────────────────────────────────────


def test_performance_profile_tau_1_is_best_only() -> None:
    """At τ=1.0 only the best arm of each task is 'solved'."""
    results = [
        _result("t1", "baseline", 1, 0.5),
        _result("t1", "true_know", 1, 0.8),
    ]
    profile = performance_profile(results, arms=["baseline", "true_know"], taus=[1.0, 10.0])
    # τ=1.0: only true_know (the best) solves
    assert profile["true_know"][1.0] == 1.0
    assert profile["baseline"][1.0] == 0.0
    # τ=10.0: both within an order of magnitude of best → solve
    assert profile["baseline"][10.0] == 1.0


# ── acceptance_report ────────────────────────────────────────────────────


def test_acceptance_report_passes_on_synthetic_matrix() -> None:
    """Plan §Sprint 8: synthetic matrix must clear all five gates."""
    tasks = [
        TaskSpec("pid_tuning",                   "minimize", "itae"),
        TaskSpec("crypto_aes128",                "maximize", "throughput"),
        TaskSpec("flash_attention",              "maximize", "tokens_per_sec"),
        TaskSpec("high_reliable_simulation",     "maximize", "reliability"),
        TaskSpec("quadruped_gait",               "maximize", "velocity"),
        TaskSpec("robot_arm_cycle_time",         "minimize", "cycle_time"),
        TaskSpec("battery_fast_charging",        "minimize", "time_to_full"),
        TaskSpec("jobshop_abz",                  "minimize", "makespan"),
        TaskSpec("topology_optimization",        "maximize", "stiffness"),
        TaskSpec("uav_inspection",               "maximize", "coverage"),
    ]
    results = run_matrix(tasks, list(ALL_ARMS), [1, 2, 3], synthetic_run_fn)
    report = acceptance_report(results)
    assert report.all_passed, [g.detail for g in report.gates if not g.passed]
    assert report.n_passed == 5


def test_acceptance_report_fails_when_true_loses_to_placebo() -> None:
    """If true_know is worse than placebo_know, the first gate must fail."""
    # Synthesise a stacked deck where placebo wins every task.
    rng_results = []
    for i in range(5):
        rng_results.append(_result(f"t{i}", "baseline", 1, 0.5))
        rng_results.append(_result(f"t{i}", "true_know", 1, 0.4))
        rng_results.append(_result(f"t{i}", "placebo_know", 1, 0.7))
        rng_results.append(_result(f"t{i}", "shuffled_know", 1, 0.55))
        rng_results.append(_result(f"t{i}", "task_pack_only", 1, 0.55))
        rng_results.append(_result(f"t{i}", "task_pack_plus_catalyst", 1, 0.55))
    report = acceptance_report(rng_results)
    failing = [g.name for g in report.gates if not g.passed]
    assert "true_know_beats_placebo" in failing
    assert not report.all_passed


# ── synthetic_run_fn ────────────────────────────────────────────────────


def test_synthetic_run_fn_deterministic() -> None:
    """Same (task, arm, seed) → same TaskRunResult."""
    ts = TaskSpec("pid_tuning", "minimize", "itae")
    a = synthetic_run_fn(ts, "true_know", 1)
    b = synthetic_run_fn(ts, "true_know", 1)
    assert a.score == b.score
    assert a.valid == b.valid


def test_synthetic_run_fn_direction_aware_for_minimize() -> None:
    """For 'minimize', true_know's score should be *lower* than baseline's."""
    ts = TaskSpec("pid_tuning", "minimize", "itae")
    means: dict[str, list[float]] = {"baseline": [], "true_know": []}
    for s in range(1, 21):
        for arm in ("baseline", "true_know"):
            r = synthetic_run_fn(ts, arm, s)
            if r.valid and r.score is not None:
                means[arm].append(r.score)
    avg_base = sum(means["baseline"]) / len(means["baseline"])
    avg_tk = sum(means["true_know"]) / len(means["true_know"])
    assert avg_tk < avg_base, (avg_tk, avg_base)


def test_synthetic_run_fn_hint_use_zero_for_baseline() -> None:
    """Baseline arm by design carries no hint."""
    ts = TaskSpec("pid_tuning", "maximize", "score")
    for s in range(1, 6):
        r = synthetic_run_fn(ts, "baseline", s)
        assert r.hint_use_rate == 0.0


# ── render_markdown + to_jsonable smoke ─────────────────────────────────


def test_render_markdown_has_required_sections() -> None:
    tasks = [TaskSpec("t1", "maximize", "score")]
    results = run_matrix(tasks, list(ALL_ARMS), [1, 2, 3], synthetic_run_fn)
    md = render_markdown(results)
    assert "Sprint 8" in md
    assert "Per-arm aggregates" in md
    assert "Acceptance gates" in md
    assert "avg_rank" in md


def test_to_jsonable_round_trip() -> None:
    tasks = [TaskSpec("t1", "maximize", "score")]
    results = run_matrix(tasks, list(ALL_ARMS), [1, 2, 3], synthetic_run_fn)
    payload = to_jsonable(results)
    # JSON serialisable
    json.dumps(payload)  # raises on failure
    assert payload["schema_version"] == "1.0"
    assert "arm_summaries" in payload
    assert "performance_profile" in payload
    assert "acceptance" in payload


# ── CLI integration ─────────────────────────────────────────────────────


def test_cli_main_with_synthetic_backend(tmp_path: Path) -> None:
    """The script's main() should exit 0 with synthetic backend + default config."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import run_ab_harness  # noqa: E402

    rc = run_ab_harness.main([
        "--seeds", "3",
        "--out-tag", "test",
        "--out-dir", str(tmp_path),
    ])
    assert rc == 0
    assert (tmp_path / "sprint8_test.json").is_file()
    assert (tmp_path / "sprint8_test.md").is_file()
    payload = json.loads((tmp_path / "sprint8_test.json").read_text())
    assert payload["acceptance"]["all_passed"] is True
