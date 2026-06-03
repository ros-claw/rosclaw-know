"""Tests for the trajectory extraction pipeline (Sprint 3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from rosclaw_know.extractors import (
    extract_candidate_patterns,
    extract_optimizer_features,
    extract_pid_features,
    extract_systems_features,
    from_baseline_archive_pair,
    summarize_diff,
)
from rosclaw_know.extractors.code_diff_summarizer import (
    _LEAK_RE,
    DiffSummary,
    _scrub_descriptions,
)
from rosclaw_know.schemas import (
    CandidatePattern,
    Mutation,
    Trajectory,
    TrajectoryStep,
)

BASELINE_ARCHIVE = Path(
    "/root/workspace/rosclaw/rosclaw_wiki/Frontier-Engineering/baseline_archive"
)
BENCHMARKS = Path(
    "/root/workspace/rosclaw/rosclaw_wiki/Frontier-Engineering/benchmarks"
)
HAS_CORPUS = BASELINE_ARCHIVE.is_dir() and BENCHMARKS.is_dir()


# ── code_diff_summarizer unit tests ────────────────────────────────────


def test_summarize_diff_detects_ki_zero() -> None:
    baseline = '''
best_gains = {
    "Kp_z": 8.0, "Ki_z": 0.5, "Kd_z": 4.0,
    "Kp_x": 0.1, "Ki_x": 0.01, "Kd_x": 0.1,
}
'''
    candidate = '''
best_gains = {
    "Kp_z": 21.0, "Ki_z": 0.0, "Kd_z": 8.0,
    "Kp_x": 2.6, "Ki_x": 0.0, "Kd_x": 1.5,
}
'''
    ds = summarize_diff(baseline, candidate)
    kinds = {(m.kind, m.target_identifier) for m in ds.mutations}
    assert ("set_parameter_zero", "Ki_z") in kinds
    assert ("set_parameter_zero", "Ki_x") in kinds


def test_summarize_diff_detects_output_clamp() -> None:
    baseline = "T_cmd = m * g + thrust_offset\n"
    candidate = "T_cmd = m * g + thrust_offset\nT_cmd = np.clip(T_cmd, 0.0, max_thrust)\n"
    ds = summarize_diff(baseline, candidate)
    kinds = {(m.kind, m.target_identifier) for m in ds.mutations}
    assert ("add_output_clamp", "T_cmd") in kinds


def test_summarize_diff_detects_time_budget() -> None:
    baseline = "for i in range(100):\n    pass\n"
    candidate = (
        "import time\nstart = time.time()\nTIME_BUDGET = 270.0\n"
        "while time.time() - start < TIME_BUDGET:\n    pass\n"
    )
    ds = summarize_diff(baseline, candidate)
    assert any(m.kind == "add_time_budget" for m in ds.mutations)


def test_summarize_diff_detects_optimizer_swap() -> None:
    baseline = "def random_gains():\n    return rng.uniform(0, 1)\n"
    candidate = (
        "def random_gains():\n    return rng.uniform(0, 1)\n"
        "# CMA-ES style evolution strategy\n"
    )
    ds = summarize_diff(baseline, candidate)
    assert any(m.kind == "swap_optimizer" for m in ds.mutations)


def test_summarize_diff_returns_deterministic_output() -> None:
    """Same input → same output, byte-identical."""
    baseline = "x = 1\n"
    candidate = "x = 1\ny = np.clip(z, 0, 1)\n"
    a = summarize_diff(baseline, candidate)
    b = summarize_diff(baseline, candidate)
    # Compare via model_dump for stable equality
    assert [m.model_dump() for m in a.mutations] == [
        m.model_dump() for m in b.mutations
    ]


def test_summarize_diff_handles_invalid_python_gracefully() -> None:
    """C++ source should not crash the AST detectors."""
    baseline = "int main() { return 0; }"
    candidate = "int main() {\n  for (int i = 0; i < 100; ++i) {}\n  return 0;\n}"
    ds = summarize_diff(baseline, candidate)  # must not raise
    assert isinstance(ds, DiffSummary)


# ── leak guard ────────────────────────────────────────────────────────


def test_scrub_descriptions_strips_float_literals() -> None:
    m = Mutation(kind="set_parameter_zero",
                 description="set Ki_z to 0.5 then Ki_x to 0.014",
                 target_identifier="Ki_z")
    scrubbed = _scrub_descriptions([m])
    assert "0.5" not in scrubbed[0].description
    assert "0.014" not in scrubbed[0].description
    assert "<value>" in scrubbed[0].description


def test_scrub_descriptions_passes_integers_through() -> None:
    """Integer 0 is structural — not a leak — and stays in the desc."""
    m = Mutation(kind="set_parameter_zero",
                 description="set integral gain to zero on Ki_z",
                 target_identifier="Ki_z")
    scrubbed = _scrub_descriptions([m])
    assert scrubbed[0].description == m.description


def test_leak_regex_catches_typical_answer_values() -> None:
    assert _LEAK_RE.search("Kp_z = 21.364")
    assert _LEAK_RE.search("Ki_x = 0.0142")
    assert not _LEAK_RE.search("set to zero")


# ── feature extractors ────────────────────────────────────────────────


def _make_traj(task_name: str, mutations: list[Mutation]) -> Trajectory:
    return Trajectory(
        trajectory_id="t",
        task_name=task_name,
        steps=[TrajectoryStep(iteration=0, score=0.1, valid=True, mutations=mutations)],
    )


def test_pid_extractor_emits_zero_integral_candidate() -> None:
    muts = [
        Mutation(kind="set_parameter_zero",
                 description="set parameter to zero on Ki_z",
                 target_identifier="Ki_z"),
    ]
    traj = _make_traj("PIDTuning", muts)
    cps = extract_pid_features(traj)
    ids = {c.id for c in cps}
    assert "candidate_zero_integral_gain_on_saturation" in ids


def test_pid_extractor_ignores_kp_zero() -> None:
    """Kp/Kd → zero are not anti-windup signals; ignore them."""
    muts = [
        Mutation(kind="set_parameter_zero",
                 description="set parameter to zero on Kp_z",
                 target_identifier="Kp_z"),
    ]
    traj = _make_traj("PIDTuning", muts)
    cps = extract_pid_features(traj)
    # Anti-windup candidate must NOT fire on Kp_z
    assert not any(c.id == "candidate_zero_integral_gain_on_saturation" for c in cps)


def test_pid_extractor_skips_non_pid_task() -> None:
    """A KernelEngineering task with no PID hint should not get PID candidates."""
    muts = [
        Mutation(kind="add_output_clamp", description="clamp x",
                 target_identifier="x"),
    ]
    traj = _make_traj("FlashAttention", muts)
    cps = extract_pid_features(traj)
    assert cps == []


def test_systems_extractor_emits_vectorize() -> None:
    muts = [
        Mutation(kind="vectorize_loop",
                 description="replaced explicit Python loop with numpy array form"),
    ]
    traj = _make_traj("Optics_phase_dammann_uniform_orders", muts)
    cps = extract_systems_features(traj)
    assert any(c.id == "candidate_vectorize_inner_loop" for c in cps)


def test_optimizer_extractor_emits_warm_start() -> None:
    muts = [
        Mutation(kind="add_initialization_seed",
                 description="seeded optimizer from a prior-best solution"),
    ]
    traj = _make_traj("PMDSimulation", muts)
    cps = extract_optimizer_features(traj)
    assert any(c.id == "candidate_warm_start_from_prior_best" for c in cps)


def test_extract_candidate_patterns_runs_all_registered() -> None:
    muts = [
        Mutation(kind="set_parameter_zero", description="set parameter to zero on Ki_z",
                 target_identifier="Ki_z"),
        Mutation(kind="vectorize_loop",
                 description="replaced explicit Python loop with numpy array form"),
    ]
    traj = _make_traj("PIDTuning", muts)
    cps = extract_candidate_patterns(traj)
    ids = {c.id for c in cps}
    # PID extractor + systems extractor should both have fired
    assert "candidate_zero_integral_gain_on_saturation" in ids
    assert "candidate_vectorize_inner_loop" in ids


# ── trajectory builder ────────────────────────────────────────────────


def test_from_baseline_archive_pair_builds_one_step_trajectory() -> None:
    traj = from_baseline_archive_pair(
        baseline_text="x = 1\n",
        candidate_text="x = 1\ny = np.clip(z, 0, 1)\n",
        task_name="PIDTuning",
        trajectory_id="r1",
        algorithm="openevolve",
        model="claude-opus-4.6",
    )
    assert traj.trajectory_id == "r1"
    assert traj.algorithm == "openevolve"
    assert traj.model == "claude-opus-4.6"
    assert len(traj.steps) == 1
    assert traj.steps[0].iteration == 0
    # Should have at least the clamp mutation
    assert any(m.kind == "add_output_clamp" for m in traj.steps[0].mutations)


def test_from_iteration_dir_handles_missing_dir(tmp_path: Path) -> None:
    """An empty run dir returns None, doesn't crash."""
    from rosclaw_know.extractors import from_iteration_dir
    assert from_iteration_dir(tmp_path, "Whatever") is None


def test_from_iteration_dir_reads_real_iterations(tmp_path: Path) -> None:
    """End-to-end: build a 3-step trajectory from synthetic
    iteration_NNN/{code.py,eval.json}."""
    import json

    from rosclaw_know.extractors import from_iteration_dir

    (tmp_path / "iteration_000").mkdir()
    (tmp_path / "iteration_000" / "code.py").write_text("x = 1\n")
    (tmp_path / "iteration_000" / "eval.json").write_text(
        json.dumps({"score": 0.10, "valid": True})
    )

    (tmp_path / "iteration_001").mkdir()
    (tmp_path / "iteration_001" / "code.py").write_text(
        "x = 1\ny = np.clip(z, 0, 1)\n"
    )
    (tmp_path / "iteration_001" / "eval.json").write_text(
        json.dumps({"score": 0.14, "valid": True})
    )

    (tmp_path / "iteration_002").mkdir()
    (tmp_path / "iteration_002" / "code.py").write_text(
        "x = 1\ny = np.clip(z, 0, 1)\nimport time\nTIME_BUDGET = 270\n"
    )
    (tmp_path / "iteration_002" / "eval.json").write_text(
        json.dumps({"score": 0.16, "valid": True})
    )

    traj = from_iteration_dir(tmp_path, task_name="TestTask")
    assert traj is not None
    assert len(traj.steps) == 3
    assert traj.steps[0].score == 0.10
    assert traj.steps[2].score == 0.16
    # best_delta = max - first = 0.16 - 0.10 = 0.06
    assert traj.best_delta is not None
    assert abs(traj.best_delta - 0.06) < 1e-9
    # Step 2 should have the time-budget mutation (introduced vs step 1)
    assert any(m.kind == "add_time_budget" for m in traj.steps[2].mutations)


# ── integration tests against real baseline_archive ──────────────────


@pytest.mark.skipif(not HAS_CORPUS, reason="baseline_archive not present")
def test_real_pid_extracts_at_least_one_candidate() -> None:
    """The claude-opus-4.6 PIDTuning best is known to contain Ki=0
    and a CMA-ES swap.  Sanity-check that we find them."""
    baseline = (BENCHMARKS / "Robotics/PIDTuning/scripts/init.py").read_text()
    best = (BASELINE_ARCHIVE / "experiment1/openevolve/claude-opus-4.6/"
            "Robotics_PIDTuning/program.py").read_text()
    traj = from_baseline_archive_pair(
        baseline_text=baseline, candidate_text=best,
        task_name="PIDTuning",
        trajectory_id="test_real_pid",
        algorithm="openevolve",
        model="claude-opus-4.6",
    )
    cps = extract_candidate_patterns(traj)
    ids = {c.id for c in cps}
    assert "candidate_zero_integral_gain_on_saturation" in ids
    assert "candidate_swap_random_search_to_structured_optimizer" in ids


@pytest.mark.skipif(not HAS_CORPUS, reason="baseline_archive not present")
def test_no_real_candidate_leaks_concrete_values() -> None:
    """For every candidate produced from real archive data, no mutation
    description contains a float literal (the leak guard)."""
    baseline = (BENCHMARKS / "Robotics/PIDTuning/scripts/init.py").read_text()
    archive_pid = list((BASELINE_ARCHIVE).rglob("Robotics_PIDTuning/program.py"))
    assert len(archive_pid) >= 5, (
        f"expected ≥5 PIDTuning programs in archive, found {len(archive_pid)}"
    )
    for prog in archive_pid:
        cand = prog.read_text()
        traj = from_baseline_archive_pair(
            baseline_text=baseline,
            candidate_text=cand,
            task_name="PIDTuning",
            trajectory_id=prog.parent.name,
        )
        for cp in extract_candidate_patterns(traj):
            for m in cp.successful_mutations + cp.failed_mutations:
                assert not _LEAK_RE.search(m.description), (
                    f"leak in {prog}: {m.description!r}"
                )


# ── schema invariants ────────────────────────────────────────────────


def test_candidate_pattern_id_must_match_pattern() -> None:
    """The id field's regex enforces ``candidate_*`` prefix."""
    with pytest.raises(Exception):  # ValidationError
        CandidatePattern(
            id="bad-prefix-here",
            task_family="x",
            diagnosis="y",
        )


def test_trajectory_step_rejects_negative_iteration() -> None:
    with pytest.raises(Exception):
        TrajectoryStep(iteration=-1)
