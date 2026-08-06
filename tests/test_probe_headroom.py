"""Tests for the headroom probe — monkeypatch the LLM + judge calls.

The probe itself is a thin shell; what matters is that:
  - the verdict thresholds match the spec (≥2.0, ≥1.0, <1.0)
  - task filter accepts both exact IDs and regex
  - missing/None scores are tolerated (aggregation only over valid)
  - the LLM call inside verify_frontier_eng is invoked with empty
    treatment_context (i.e. unaided baseline, not injection)
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "probe_headroom",
        Path(__file__).resolve().parent.parent / "scripts" / "probe_headroom.py",
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def ph():
    return _load_module()


class TestVerdictThresholds:
    def test_author_curated_at_threshold(self, ph):
        assert ph._verdict(2.0) == "AUTHOR_CURATED"
        assert ph._verdict(5.5) == "AUTHOR_CURATED"

    def test_maybe_band(self, ph):
        assert ph._verdict(1.0) == "MAYBE"
        assert ph._verdict(1.99) == "MAYBE"

    def test_skip_curated_below_one(self, ph):
        assert ph._verdict(0.99) == "SKIP_CURATED"
        assert ph._verdict(0.0) == "SKIP_CURATED"


class TestScoreRegex:
    def test_basic_pattern(self, ph):
        m = ph._SCORE_RX.search("SCORE=9 REASON=hits the canonical fix")
        assert m and int(m.group(1)) == 9
        assert m.group(2).startswith("hits")

    def test_colon_form(self, ph):
        m = ph._SCORE_RX.search("SCORE:7  REASON: family-level fix only")
        assert m and int(m.group(1)) == 7

    def test_score_only(self, ph):
        # REASON is optional in the regex
        m = ph._SCORE_RX.search("SCORE=4")
        assert m and int(m.group(1)) == 4


class TestTaskResolution:
    def test_exact_match(self, ph):
        # Build a fake verify module with three tasks.
        class V:
            TASKS = [
                {"task_id": "TASK_001_PIDTuning", "symptom": "x", "evaluation_hint": "y"},
                {"task_id": "TASK_W_002_GradExplosionRL", "symptom": "x", "evaluation_hint": "y"},
                {"task_id": "TASK_006_FlashAttention", "symptom": "x", "evaluation_hint": "y"},
            ]

        sel = ph._resolve_task_ids(V, ["TASK_001_PIDTuning"])
        assert [t["task_id"] for t in sel] == ["TASK_001_PIDTuning"]

    def test_regex_match(self, ph):
        class V:
            TASKS = [
                {"task_id": "TASK_001_PIDTuning"},
                {"task_id": "TASK_W_002_GradExplosionRL"},
                {"task_id": "TASK_006_FlashAttention"},
            ]

        sel = ph._resolve_task_ids(V, [r"^TASK_W_"])
        assert [t["task_id"] for t in sel] == ["TASK_W_002_GradExplosionRL"]

    def test_empty_filter_returns_all(self, ph):
        class V:
            TASKS = [{"task_id": "A"}, {"task_id": "B"}]

        sel = ph._resolve_task_ids(V, [])
        assert len(sel) == 2


class TestProbeAggregation:
    """End-to-end probe() with monkeypatched LLM + judge — no network."""

    def test_two_seeds_two_tasks_aggregates(self, ph, monkeypatch):
        # Fake verify module with two tasks.
        class FakeVerify:
            TASKS = [
                {
                    "task_id": "TASK_FAKE_HIGH",
                    "symptom": "fake high-knowledge symptom",
                    "evaluation_hint": "must name X",
                },
                {
                    "task_id": "TASK_FAKE_LOW",
                    "symptom": "fake low-knowledge symptom",
                    "evaluation_hint": "must name Y",
                },
            ]

            # Vary reply per (task, seed) so we can prove the call pattern.
            @staticmethod
            def _call_agent(symptom, treatment_context, *, temperature, seed):
                # Critical: probe must pass empty treatment_context (unaided baseline).
                assert treatment_context == "", "probe must not inject treatment context"
                return f"reply for {symptom!r} seed={seed}"

        monkeypatch.setattr(ph, "_load_verify_module", lambda: FakeVerify)

        # Fake judge: HIGH always scores 10 (LLM saturates), LOW scores 5.
        def fake_judge(symptom, hint, response, seed):
            score = 10 if "high" in symptom else 5
            return {"score": score, "reason": "stub", "raw": "stub"}

        monkeypatch.setattr(ph, "_judge_one", fake_judge)

        report = ph.probe(
            task_ids=["TASK_FAKE_HIGH", "TASK_FAKE_LOW"],
            seeds=[1, 2, 3],
            temperature=0.3,
            skip_judge=False,
        )
        assert report["task_count"] == 2
        h = report["per_task"]["TASK_FAKE_HIGH"]
        low_result = report["per_task"]["TASK_FAKE_LOW"]
        assert h["control_mean"] == 10.0
        assert h["headroom"] == 0.0
        assert h["verdict"] == "SKIP_CURATED"  # LLM-saturated → don't author curated
        assert low_result["control_mean"] == 5.0
        assert low_result["headroom"] == 5.0
        assert low_result["verdict"] == "AUTHOR_CURATED"  # gap big enough → worth authoring

    def test_skip_judge_returns_lengths_only(self, ph, monkeypatch):
        class FakeVerify:
            TASKS = [{"task_id": "TASK_X", "symptom": "s", "evaluation_hint": "h"}]

            @staticmethod
            def _call_agent(symptom, treatment_context, *, temperature, seed):
                assert treatment_context == ""
                return "x" * (seed * 100)

        monkeypatch.setattr(ph, "_load_verify_module", lambda: FakeVerify)
        # Sanity: _judge_one would crash if called — confirm skip path doesn't.
        monkeypatch.setattr(ph, "_judge_one", lambda *a, **k: pytest.fail("judge called"))

        report = ph.probe(task_ids=[], seeds=[1, 2], temperature=0.3, skip_judge=True)
        x = report["per_task"]["TASK_X"]
        assert "control_mean" not in x  # no aggregation without judge
        assert x["seeds_run"] == 2
        assert x["rows"][0]["reply_len"] == 100
        assert x["rows"][1]["reply_len"] == 200

    def test_none_scores_tolerated(self, ph, monkeypatch):
        class FakeVerify:
            TASKS = [{"task_id": "TASK_X", "symptom": "s", "evaluation_hint": "h"}]

            @staticmethod
            def _call_agent(symptom, treatment_context, *, temperature, seed):
                return "reply"

        monkeypatch.setattr(ph, "_load_verify_module", lambda: FakeVerify)

        # One valid score, one None — aggregate over valid only.
        calls = iter([
            {"score": 7, "reason": "ok", "raw": ""},
            {"score": None, "reason": "unparseable", "raw": ""},
        ])
        monkeypatch.setattr(ph, "_judge_one", lambda *a, **k: next(calls))

        report = ph.probe(task_ids=[], seeds=[1, 2], temperature=0.3, skip_judge=False)
        x = report["per_task"]["TASK_X"]
        assert x["valid_scores"] == 1
        assert x["control_mean"] == 7.0
        # headroom = 10 - 7 = 3.0 → AUTHOR_CURATED
        assert x["verdict"] == "AUTHOR_CURATED"

    def test_no_matching_tasks_exits(self, ph, monkeypatch):
        class FakeVerify:
            TASKS = [{"task_id": "TASK_A"}]

        monkeypatch.setattr(ph, "_load_verify_module", lambda: FakeVerify)
        with pytest.raises(SystemExit):
            ph.probe(task_ids=["DOES_NOT_EXIST"], seeds=[1], temperature=0.3, skip_judge=True)
