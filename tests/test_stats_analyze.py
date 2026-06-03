"""Tests for ``rosclaw_know.stats_analyze``."""
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

from rosclaw_know.stats_analyze import (  # noqa: E402
    DEGRADING_SLOPE,
    IMPROVING_SLOPE,
    _linear_slope,
    analyze_trends,
    load_history,
    render_markdown_report,
    snapshot_stats,
)


def _stats_for(uplifts: dict[str, float], n: int = 10, win_rate: float = 0.5) -> dict:
    return {
        pid: {"n": n, "avg_uplift": u, "win_rate": win_rate, "last_seen_iso": "2026-05-18T10:00:00Z"}
        for pid, u in uplifts.items()
    }


def _snap(stats: dict) -> dict:
    return {"captured_at": "2026-05-18T10:00:00Z", "stats": stats}


class LinearSlopeTest(unittest.TestCase):
    def test_constant_zero_slope(self) -> None:
        self.assertEqual(_linear_slope([0.5, 0.5, 0.5, 0.5]), 0.0)

    def test_monotone_increase_positive(self) -> None:
        slope = _linear_slope([0.1, 0.2, 0.3, 0.4])
        self.assertGreater(slope, 0)
        self.assertAlmostEqual(slope, 0.1, places=4)

    def test_monotone_decrease_negative(self) -> None:
        slope = _linear_slope([0.4, 0.3, 0.2, 0.1])
        self.assertLess(slope, 0)
        self.assertAlmostEqual(slope, -0.1, places=4)

    def test_single_value_zero(self) -> None:
        self.assertEqual(_linear_slope([0.5]), 0.0)


class ClassifyTrendTest(unittest.TestCase):
    def test_insufficient_samples(self) -> None:
        snaps = [_snap(_stats_for({"p": 0.2})), _snap(_stats_for({"p": 0.3}))]
        # Only 2 samples → below MIN_SAMPLES_FOR_TREND
        trends = analyze_trends(snaps)
        self.assertEqual(trends["p"].trend, "insufficient")
        self.assertEqual(trends["p"].samples, 2)

    def test_improving(self) -> None:
        snaps = [
            _snap(_stats_for({"p": 0.05})),
            _snap(_stats_for({"p": 0.12})),
            _snap(_stats_for({"p": 0.18})),
            _snap(_stats_for({"p": 0.25})),
        ]
        trends = analyze_trends(snaps)
        self.assertEqual(trends["p"].trend, "improving")
        self.assertGreater(trends["p"].slope, IMPROVING_SLOPE)

    def test_degrading(self) -> None:
        snaps = [
            _snap(_stats_for({"p": 0.25})),
            _snap(_stats_for({"p": 0.18})),
            _snap(_stats_for({"p": 0.12})),
            _snap(_stats_for({"p": 0.05})),
        ]
        trends = analyze_trends(snaps)
        self.assertEqual(trends["p"].trend, "degrading")
        self.assertLess(trends["p"].slope, DEGRADING_SLOPE)

    def test_flat_within_band(self) -> None:
        # Tiny noise around 0.10, slope well within ±IMPROVING_SLOPE
        snaps = [
            _snap(_stats_for({"p": 0.10})),
            _snap(_stats_for({"p": 0.101})),
            _snap(_stats_for({"p": 0.099})),
            _snap(_stats_for({"p": 0.100})),
        ]
        trends = analyze_trends(snaps)
        self.assertEqual(trends["p"].trend, "flat")

    def test_window_takes_tail_only(self) -> None:
        # First 5 are degrading badly; last 4 are flat — window=4 should see "flat"
        snaps = (
            [_snap(_stats_for({"p": 0.5 - 0.1 * i})) for i in range(5)]
            + [_snap(_stats_for({"p": 0.05})) for _ in range(4)]
        )
        trends = analyze_trends(snaps, window=4)
        self.assertEqual(trends["p"].trend, "flat")
        self.assertEqual(trends["p"].samples, 4)


class HistoryAndSnapshotTest(unittest.TestCase):
    def test_snapshot_then_load(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            payload = _stats_for({"p": 0.3, "q": -0.1})
            path = snapshot_stats(payload, history_dir=tdp)
            self.assertTrue(path.exists())
            snaps = load_history(history_dir=tdp)
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["stats"], payload)

    def test_load_missing_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td) / "ghost"
            self.assertEqual(load_history(history_dir=tdp), [])

    def test_skip_unreadable_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "stats-bad.json").write_text("{not json", encoding="utf-8")
            good = tdp / "stats-good.json"
            good.write_text(json.dumps(_snap(_stats_for({"p": 0.1}))), encoding="utf-8")
            snaps = load_history(history_dir=tdp)
            self.assertEqual(len(snaps), 1)


class MarkdownRenderTest(unittest.TestCase):
    def test_groups_by_trend(self) -> None:
        snaps_for = lambda series, pid="p": [_snap(_stats_for({pid: u})) for u in series]
        all_snaps = (
            snaps_for([0.05, 0.12, 0.18, 0.25], "gainer")
            + snaps_for([0.25, 0.18, 0.12, 0.05], "loser")
            + snaps_for([0.10, 0.101, 0.099, 0.100], "stable")
        )
        # interleave so analyze_trends sees them
        flat: list = []
        for i in range(4):
            for series in (
                "gainer", "loser", "stable",
            ):
                if series == "gainer":
                    flat.append(_snap(_stats_for({"gainer": [0.05, 0.12, 0.18, 0.25][i]})))
                elif series == "loser":
                    flat.append(_snap(_stats_for({"loser": [0.25, 0.18, 0.12, 0.05][i]})))
                else:
                    flat.append(_snap(_stats_for({"stable": [0.10, 0.101, 0.099, 0.100][i]})))

        trends = analyze_trends(flat)
        md = render_markdown_report(trends)
        self.assertIn("## Improving", md)
        self.assertIn("## Degrading", md)
        self.assertIn("gainer", md)
        self.assertIn("loser", md)

    def test_empty_trends_still_renders(self) -> None:
        md = render_markdown_report({})
        self.assertIn("Patterns tracked: **0**", md)


if __name__ == "__main__":
    unittest.main()
