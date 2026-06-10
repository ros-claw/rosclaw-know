"""Tests for the server-PID-bound paired harness orchestrator.

The harness is a thin wrapper around verify_frontier_eng.py + judge_frontier_eng.py
that captures server identity, hashes outputs, and records reproducibility
metadata. Tests mock the subprocess layer; the actual LLM/judge calls are
covered by their own scripts.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_paired_ab", Path(__file__).resolve().parent.parent / "scripts" / "run_paired_ab.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def rpa():
    return _load_module()


def test_sha256_file_stable(rpa, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello world", encoding="utf-8")
    a = rpa._sha256_file(f)
    b = rpa._sha256_file(f)
    assert a == b
    assert len(a) == 64
    # Same content elsewhere → same hash
    g = tmp_path / "y.txt"
    g.write_text("hello world", encoding="utf-8")
    assert rpa._sha256_file(g) == a


def test_sha256_file_distinguishes_content(rpa, tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    assert rpa._sha256_file(a) != rpa._sha256_file(b)


def test_capture_server_meta_offline(rpa):
    """When no server is reachable, _capture_server_meta degrades gracefully."""
    meta = rpa._capture_server_meta("http://127.0.0.1:1", "k")
    # /healthz must have failed
    assert "healthz_error" in meta
    # PID may be None (no listener on :1)
    assert meta.get("server_pid") is None
    # Model identity is always captured regardless of network
    assert "deepseek_base_url" in meta
    assert "deepseek_muse_model" in meta


def test_run_one_seed_records_hashes(rpa, tmp_path, monkeypatch):
    """Verify _run_one_seed walks output dir + sha256s control/treatment files."""

    # Fake verify_frontier_eng.py: write 2 task pairs into the output dir.
    def fake_call(cmd, stdout=None, stderr=None, cwd=None):
        # cmd is the verify args. Parse --out-dir.
        out_dir = Path(cmd[cmd.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "TASK_001_PIDTuning.control.txt").write_text(
            "control reply pid", encoding="utf-8"
        )
        (out_dir / "TASK_001_PIDTuning.treatment.txt").write_text(
            "treatment reply pid", encoding="utf-8"
        )
        (out_dir / "TASK_002_QuadrupedGait.control.txt").write_text(
            "control reply quad", encoding="utf-8"
        )
        (out_dir / "TASK_002_QuadrupedGait.treatment.txt").write_text(
            "treatment reply quad", encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(rpa.subprocess, "call", fake_call)

    seed_dir = tmp_path / "seed_1"
    out = rpa._run_one_seed(1, seed_dir, "http://127.0.0.1:1", temperature=0.3)
    assert out["seed"] == 1
    assert out["return_code"] == 0
    assert out["task_count"] == 2
    by_task = {t["task_id"]: t for t in out["tasks"]}
    assert "TASK_001_PIDTuning" in by_task
    assert len(by_task["TASK_001_PIDTuning"]["control_response_hash"]) == 64
    assert len(by_task["TASK_001_PIDTuning"]["treatment_response_hash"]) == 64
    # Different content → different hashes
    assert (
        by_task["TASK_001_PIDTuning"]["control_response_hash"]
        != by_task["TASK_001_PIDTuning"]["treatment_response_hash"]
    )
    # Same control content for two tasks would collide; the contents differ.
    assert (
        by_task["TASK_001_PIDTuning"]["control_response_hash"]
        != by_task["TASK_002_QuadrupedGait"]["control_response_hash"]
    )


def test_main_end_to_end_skip_judge(rpa, tmp_path, monkeypatch, capsys):
    """End-to-end smoke: --skip-judge run produces a valid harness_meta.json."""
    fake_label = "test_smoke_n1"
    fake_harness_root = tmp_path / "paired_ab"
    monkeypatch.setattr(rpa, "HARNESS_ROOT", fake_harness_root)
    monkeypatch.setattr(rpa, "FROZEN_ROOT", tmp_path / "frozen")

    # Fake subprocess.call → write a single task pair
    def fake_call(cmd, stdout=None, stderr=None, cwd=None):
        out_dir = Path(cmd[cmd.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "TASK_001.control.txt").write_text("c", encoding="utf-8")
        (out_dir / "TASK_001.treatment.txt").write_text("t", encoding="utf-8")
        return 0

    monkeypatch.setattr(rpa.subprocess, "call", fake_call)

    monkeypatch.setattr(
        sys, "argv",
        ["run_paired_ab.py", "--label", fake_label,
         "--how-base", "http://127.0.0.1:1",
         "--seeds", "1",
         "--skip-judge"]
    )
    rc = rpa.main()
    assert rc == 0

    meta_path = fake_harness_root / fake_label / "harness_meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["label"] == fake_label
    assert meta["seeds"] == [1]
    assert meta["bundle"]["label"] is None  # no bundle pointer passed
    assert meta["server_pid_drifted_mid_run"] is False
    assert meta["per_seed"][0]["task_count"] == 1


def test_main_refuses_to_overwrite(rpa, tmp_path, monkeypatch):
    """If the run-label dir already exists, refuse to clobber it."""
    fake_label = "already_done"
    fake_harness_root = tmp_path / "paired_ab"
    (fake_harness_root / fake_label).mkdir(parents=True)
    monkeypatch.setattr(rpa, "HARNESS_ROOT", fake_harness_root)

    monkeypatch.setattr(
        sys, "argv",
        ["run_paired_ab.py", "--label", fake_label,
         "--how-base", "http://127.0.0.1:1",
         "--seeds", "1",
         "--skip-judge"]
    )
    with pytest.raises(SystemExit):
        rpa.main()
