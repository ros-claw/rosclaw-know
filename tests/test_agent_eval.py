"""Tests for the Phase 9 real-agent A/B harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rosclaw_know.agent_eval.backends import SyntheticBackend, build_backend
from rosclaw_know.agent_eval.report_writer import write_report
from rosclaw_know.agent_eval.synthetic_tasks import (
    SCORING_FNS,
    TASK_STUBS,
    get_scoring_fn,
    score_cartpole_pid,
    score_plc_anomaly,
    score_quadrotor_altitude,
)
from rosclaw_know.agent_eval.task_loader import load_task, load_tasks
from rosclaw_know.agent_eval.task_runner import run_one, run_one_with_code
from rosclaw_know.agent_eval.types import EvalTask


@pytest.fixture
def eval_task() -> EvalTask:
    return EvalTask(
        task_id="quadrotor_altitude",
        description="hold altitude",
        entrypoint="control",
        scoring_fn_name="score_quadrotor_altitude",
        objective_direction="maximize",
        metric_name="tracking_accuracy",
        max_iters=20,
        params={
            "mass": 1.0,
            "gravity": 9.81,
            "target_altitude": 10.0,
            "wind_sigma": 0.5,
            "dt": 0.05,
            "total_time": 2.0,
            "timeout": 5.0,
        },
    )


def test_load_tasks_finds_defaults():
    tasks = load_tasks("data/eval_tasks/*.yaml")
    ids = {t.task_id for t in tasks}
    assert ids == {
        "quadrotor_altitude",
        "pendulum_swingup",
        "cartpole_pid",
        "lunar_lander",
        "plc_anomaly",
    }


def test_load_task_rejects_missing_key(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("task_id: only_id\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_task(path)


def test_load_task_rejects_bad_direction(tmp_path):
    path = tmp_path / "bad_dir.yaml"
    path.write_text(
        "task_id: t\ndescription: d\nentrypoint: e\nscoring_fn_name: s\n"
        "objective_direction: bigger\nmetric_name: m\nmax_iters: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_task(path)


def test_get_scoring_fn_known():
    assert get_scoring_fn("score_quadrotor_altitude") is score_quadrotor_altitude


def test_get_scoring_fn_unknown():
    with pytest.raises(ValueError):
        get_scoring_fn("score_missing")


def test_all_task_scoring_fns_registered():
    tasks = load_tasks("data/eval_tasks/*.yaml")
    for task in tasks:
        assert task.scoring_fn_name in SCORING_FNS, task.task_id


def test_scoring_deterministic(eval_task):
    code = TASK_STUBS[eval_task.task_id]["true_know"]
    a = score_quadrotor_altitude(1, code, eval_task.params)
    b = score_quadrotor_altitude(1, code, eval_task.params)
    assert a == b


def test_true_know_beats_baseline(eval_task):
    true_code = TASK_STUBS[eval_task.task_id]["true_know"]
    base_code = TASK_STUBS[eval_task.task_id]["baseline"]
    true_score, _ = score_quadrotor_altitude(1, true_code, eval_task.params)
    base_score, _ = score_quadrotor_altitude(1, base_code, eval_task.params)
    assert true_score > base_score


def test_cartpole_mass_change_respected():
    code = TASK_STUBS["cartpole_pid"]["true_know"]
    params = {
        "mass_cart": 2.0,
        "mass_pole": 0.2,
        "length": 0.5,
        "gravity": 9.81,
        "dt": 0.02,
        "total_time": 2.0,
        "angle_limit": 0.2,
        "position_limit": 2.4,
        "timeout": 5.0,
    }
    score, _ = score_cartpole_pid(1, code, params)
    assert 0.0 <= score <= 1.0


def test_plc_f1_perfect_and_zero():
    params = {
        "n_lines": 120,
        "n_anomalies": 5,
        "spike_magnitude": 8.0,
        "noise_sigma": 0.3,
        "timeout": 5.0,
    }
    perfect = TASK_STUBS["plc_anomaly"]["true_know"]
    empty = TASK_STUBS["plc_anomaly"]["baseline"]
    perfect_score, _ = score_plc_anomaly(1, perfect, params)
    empty_score, _ = score_plc_anomaly(1, empty, params)
    assert perfect_score == pytest.approx(1.0, abs=0.05)
    assert empty_score == 0.0


def test_runner_invalid_code_is_invalid(eval_task):
    class BadBackend:
        def run(self, task, arm, seed):
            return "def control(state, t, params):\n    return unknown_var\n"

    result = run_one(eval_task, BadBackend(), "baseline", 1)
    assert not result.valid
    assert result.score is None


def test_runner_with_code_returns_code(eval_task):
    backend = SyntheticBackend()
    result, code = run_one_with_code(eval_task, backend, "true_know", 1)
    assert result.valid
    assert code.startswith("def control")


def test_synthetic_backend_arm_mapping(eval_task):
    backend = SyntheticBackend()
    baseline = backend.run(eval_task, "baseline", 1)
    true_know = backend.run(eval_task, "true_know", 1)
    assert baseline != true_know
    placebo = backend.run(eval_task, "placebo_know", 1)
    assert placebo == baseline


def test_report_writer_creates_files(tmp_path):
    from rosclaw_know.ab_harness import TaskRunResult

    results = [
        TaskRunResult(
            task_id="t1",
            arm="baseline",
            seed=1,
            score=0.2,
            objective_direction="maximize",
            valid=True,
        ),
        TaskRunResult(
            task_id="t1",
            arm="true_know",
            seed=1,
            score=0.8,
            objective_direction="maximize",
            valid=True,
        ),
    ]
    codes = {
        ("t1", "baseline", 1): "def control(): return 0",
        ("t1", "true_know", 1): "def control(): return 1",
    }
    out = write_report("test_label", results, codes)
    assert (out / "results.jsonl").exists()
    assert (out / "trials.jsonl").exists()
    assert (out / "summary.json").exists()
    assert (out / "summary.md").exists()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["n_trials"] == 2


def test_build_backend_unknown():
    with pytest.raises(ValueError):
        build_backend("unknown")


def test_cli_synthetic_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/agent_eval_runner.py",
            "--backend",
            "synthetic",
            "--seeds",
            "2",
            "--label",
            "test_smoke",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    out_dir = Path("data/benchmarks/phase9_real_agent/test_smoke")
    assert (out_dir / "summary.json").exists()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    true_rank = summary["arm_summaries"]["true_know"]["avg_rank"]
    base_rank = summary["arm_summaries"]["baseline"]["avg_rank"]
    assert true_rank < base_rank
