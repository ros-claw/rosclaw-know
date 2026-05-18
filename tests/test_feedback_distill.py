"""Tests for ``rosclaw_know.feedback_distill``.

These tests cover the pure aggregation path — no LLM, no SeekDB. They write
synthetic outcome JSONL files and verify the produced ``pattern_metrics.json``
matches the expected shape and numbers.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("ROSCLAW_KNOW_MOCK_LLM", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.feedback_distill import (  # noqa: E402
    MIN_SAMPLE_SIZE,
    WIN_DELTA_THRESHOLD,
    aggregate,
    distill,
    is_demoted,
    write_metrics,
)


def _outcome(
    *, pid: str, delta: float, ts: str, **extra
) -> dict:
    """Build one synthetic outcome record with defaults for optional fields."""
    base = {
        "injection_id": f"id-{pid}-{ts}",
        "symptom": "synthetic",
        "pattern_id": pid,
        "similarity": 0.7,
        "pre_score": 0.4,
        "post_score": 0.4 + delta,
        "delta_score": delta,
        "iterations_to_resolve": 3,
        "agent_notes": None,
        "ts": ts,
    }
    base.update(extra)
    return base


class AggregateTest(unittest.TestCase):
    def test_empty_input(self) -> None:
        metrics = aggregate(iter(()))
        self.assertEqual(metrics, {})

    def test_single_pattern_one_record(self) -> None:
        rec = _outcome(pid="anti_windup_pid", delta=0.12, ts="2026-05-18T10:00:00Z")
        metrics = aggregate([rec])
        self.assertIn("anti_windup_pid", metrics)
        m = metrics["anti_windup_pid"]
        self.assertEqual(m.n, 1)
        self.assertAlmostEqual(m.uplift_mean, 0.12, places=4)
        # n=1 collapses to std=0 to avoid StatisticsError.
        self.assertEqual(m.uplift_std, 0.0)
        # 0.12 > WIN_DELTA_THRESHOLD ⇒ win_rate == 1.
        self.assertEqual(m.win_rate, 1.0)
        self.assertEqual(m.last_seen, "2026-05-18T10:00:00Z")

    def test_win_rate_threshold(self) -> None:
        """Deltas equal to WIN_DELTA_THRESHOLD should NOT count as wins (strict >)."""
        recs = [
            _outcome(pid="p", delta=WIN_DELTA_THRESHOLD, ts="2026-05-18T10:00:00Z"),
            _outcome(pid="p", delta=WIN_DELTA_THRESHOLD + 0.01, ts="2026-05-18T11:00:00Z"),
            _outcome(pid="p", delta=-0.01, ts="2026-05-18T12:00:00Z"),
        ]
        metrics = aggregate(recs)
        self.assertEqual(metrics["p"].n, 3)
        self.assertAlmostEqual(metrics["p"].win_rate, 1 / 3, places=4)

    def test_last_seen_is_max(self) -> None:
        recs = [
            _outcome(pid="p", delta=0.1, ts="2026-05-18T10:00:00Z"),
            _outcome(pid="p", delta=0.2, ts="2026-05-18T12:00:00Z"),
            _outcome(pid="p", delta=0.0, ts="2026-05-18T11:00:00Z"),
        ]
        metrics = aggregate(recs)
        self.assertEqual(metrics["p"].last_seen, "2026-05-18T12:00:00Z")

    def test_required_fields_skip(self) -> None:
        """Records missing pattern_id / delta_score / ts must be skipped, not crash."""
        bad = {"pattern_id": "p", "ts": "2026-05-18T10:00:00Z"}  # no delta_score
        good = _outcome(pid="p", delta=0.2, ts="2026-05-18T10:00:00Z")
        metrics = aggregate([bad, good])
        self.assertEqual(metrics["p"].n, 1)


class DemotionTest(unittest.TestCase):
    def test_demote_requires_min_samples(self) -> None:
        """Even very negative uplift_mean does not demote at small sample size."""
        recs = [
            _outcome(pid="bad", delta=-0.5, ts=f"2026-05-{i:02d}T10:00:00Z")
            for i in range(1, MIN_SAMPLE_SIZE)
        ]
        metrics = aggregate(recs)
        self.assertEqual(metrics["bad"].n, MIN_SAMPLE_SIZE - 1)
        self.assertFalse(is_demoted(metrics["bad"]))

    def test_demote_fires_above_min_samples(self) -> None:
        recs = [
            _outcome(pid="bad", delta=-0.5, ts=f"2026-05-{i:02d}T10:00:00Z")
            for i in range(1, MIN_SAMPLE_SIZE + 2)
        ]
        metrics = aggregate(recs)
        self.assertTrue(is_demoted(metrics["bad"]))

    def test_no_demote_for_positive_uplift(self) -> None:
        recs = [
            _outcome(pid="good", delta=0.3, ts=f"2026-05-{i:02d}T10:00:00Z")
            for i in range(1, MIN_SAMPLE_SIZE + 5)
        ]
        metrics = aggregate(recs)
        self.assertFalse(is_demoted(metrics["good"]))


class WriteMetricsTest(unittest.TestCase):
    def test_serialization_roundtrip(self) -> None:
        rec = _outcome(pid="p", delta=0.15, ts="2026-05-18T10:00:00Z")
        metrics = aggregate([rec])
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "metrics.json"
            write_metrics(metrics, out)
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("p", payload["patterns"])
        self.assertAlmostEqual(payload["patterns"]["p"]["uplift_mean"], 0.15, places=4)


class DistillEndToEndTest(unittest.TestCase):
    def test_distill_reads_jsonl_and_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            exports = Path(td) / "exports"
            exports.mkdir()
            day1 = exports / "outcomes-20260518.jsonl"
            day1.write_text(
                "\n".join(json.dumps(o) for o in [
                    _outcome(pid="p1", delta=0.20, ts="2026-05-18T10:00:00Z"),
                    _outcome(pid="p1", delta=0.10, ts="2026-05-18T11:00:00Z"),
                    _outcome(pid="p2", delta=-0.15, ts="2026-05-18T12:00:00Z"),
                ]) + "\n",
                encoding="utf-8",
            )
            out = Path(td) / "metrics.json"
            metrics = distill(exports_dir=exports, out_path=out)

        self.assertEqual(set(metrics.keys()), {"p1", "p2"})
        self.assertEqual(metrics["p1"].n, 2)
        self.assertAlmostEqual(metrics["p1"].uplift_mean, 0.15, places=4)
        self.assertEqual(metrics["p1"].win_rate, 1.0)
        self.assertEqual(metrics["p2"].n, 1)
        self.assertAlmostEqual(metrics["p2"].uplift_mean, -0.15, places=4)

    def test_distill_missing_dir_writes_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ghost = Path(td) / "does_not_exist"
            out = Path(td) / "metrics.json"
            metrics = distill(exports_dir=ghost, out_path=out)
            self.assertEqual(metrics, {})
            self.assertTrue(out.exists())  # still writes the empty envelope


if __name__ == "__main__":
    unittest.main()
