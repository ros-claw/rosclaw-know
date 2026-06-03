"""Tests for ``rosclaw_know.bridge_reweighter``.

Exercises the merge logic without any real wiki / SeekDB / asset paths —
all bridge_index and pattern_metrics fixtures are built in temp dirs.
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

from rosclaw_know.bridge_reweighter import reweight_bridge_index  # noqa: E402
from rosclaw_know.feedback_distill import MIN_SAMPLE_SIZE  # noqa: E402


def _write_bridge(path: Path, clusters: dict) -> None:
    path.write_text(
        json.dumps({"symptom_clusters": clusters}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_metrics(path: Path, patterns: dict) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "patterns": patterns}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_bridge(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(pid: str, n: int, uplift: float, win_rate: float, last: str = "2026-05-18T10:00:00Z") -> dict:
    return {
        "pattern_id": pid,
        "n": n,
        "uplift_mean": uplift,
        "uplift_std": 0.05,
        "win_rate": win_rate,
        "last_seen": last,
    }


class ReweightTest(unittest.TestCase):
    def test_no_metrics_clears_stale_fields(self) -> None:
        """If no pattern has samples, any previous uplift fields are stripped."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            _write_bridge(bridge, {
                "c1": {
                    "standard_name": "stale cluster",
                    "associated_patterns": ["p1"],
                    "uplift_mean": 0.42,        # stale
                    "uplift_n": 9,              # stale
                    "win_rate": 0.7,            # stale
                    "priority": -1,             # stale demotion
                },
            })
            _write_metrics(metrics, {})
            stats = reweight_bridge_index(bridge_path=bridge, metrics_path=metrics)
            bridge_after = _read_bridge(bridge)

        cluster = bridge_after["symptom_clusters"]["c1"]
        for stale in ("uplift_mean", "uplift_n", "win_rate", "priority"):
            self.assertNotIn(stale, cluster)
        self.assertEqual(stats["clusters_demoted"], 0)

    def test_positive_uplift_merges(self) -> None:
        """Two contributing patterns produce an n-weighted mean."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            _write_bridge(bridge, {
                "c1": {
                    "standard_name": "cluster",
                    "associated_patterns": ["a", "b"],
                },
            })
            _write_metrics(metrics, {
                "a": _metric("a", n=10, uplift=0.20, win_rate=0.80),
                "b": _metric("b", n=5,  uplift=0.10, win_rate=0.40),
            })
            reweight_bridge_index(bridge_path=bridge, metrics_path=metrics)
            bridge_after = _read_bridge(bridge)

        c = bridge_after["symptom_clusters"]["c1"]
        # (10*0.20 + 5*0.10) / 15 = 2.5/15 = 0.1667
        self.assertAlmostEqual(c["uplift_mean"], 0.1667, places=4)
        self.assertEqual(c["uplift_n"], 15)
        # (10*0.80 + 5*0.40) / 15 = 10/15 = 0.6667
        self.assertAlmostEqual(c["win_rate"], 0.6667, places=4)
        self.assertNotIn("priority", c)

    def test_demote_when_every_contrib_negative_and_n_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            _write_bridge(bridge, {
                "c1": {
                    "standard_name": "loser cluster",
                    "associated_patterns": ["bad1", "bad2"],
                },
            })
            _write_metrics(metrics, {
                "bad1": _metric("bad1", n=MIN_SAMPLE_SIZE + 5, uplift=-0.20, win_rate=0.10),
                "bad2": _metric("bad2", n=MIN_SAMPLE_SIZE + 1, uplift=-0.15, win_rate=0.15),
            })
            stats = reweight_bridge_index(bridge_path=bridge, metrics_path=metrics)
            bridge_after = _read_bridge(bridge)

        c = bridge_after["symptom_clusters"]["c1"]
        self.assertEqual(c["priority"], -1)
        self.assertEqual(stats["clusters_demoted"], 1)

    def test_partial_loser_not_demoted(self) -> None:
        """If at least one contributing pattern is still winning, do NOT demote."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            _write_bridge(bridge, {
                "c1": {
                    "standard_name": "mixed cluster",
                    "associated_patterns": ["bad", "good"],
                },
            })
            _write_metrics(metrics, {
                "bad":  _metric("bad",  n=MIN_SAMPLE_SIZE + 5, uplift=-0.20, win_rate=0.10),
                "good": _metric("good", n=MIN_SAMPLE_SIZE + 5, uplift=+0.15, win_rate=0.65),
            })
            reweight_bridge_index(bridge_path=bridge, metrics_path=metrics)
            bridge_after = _read_bridge(bridge)

        c = bridge_after["symptom_clusters"]["c1"]
        self.assertNotIn("priority", c)

    def test_idempotent_writes(self) -> None:
        """Running reweight twice with the same inputs should not touch the file twice."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            _write_bridge(bridge, {
                "c1": {
                    "standard_name": "cluster",
                    "associated_patterns": ["a"],
                },
            })
            _write_metrics(metrics, {"a": _metric("a", n=4, uplift=0.20, win_rate=0.50)})

            first = reweight_bridge_index(bridge_path=bridge, metrics_path=metrics)
            mtime1 = bridge.stat().st_mtime_ns
            second = reweight_bridge_index(bridge_path=bridge, metrics_path=metrics)
            mtime2 = bridge.stat().st_mtime_ns

        # First pass writes (positive touch); second pass is a no-op.
        self.assertGreater(first["clusters_touched"], 0)
        self.assertEqual(second["clusters_touched"], 0)
        self.assertEqual(mtime1, mtime2)

    def test_missing_bridge_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            stats = reweight_bridge_index(
                bridge_path=Path(td) / "missing.json",
                metrics_path=Path(td) / "missing_metrics.json",
            )
        self.assertEqual(stats["clusters_total"], 0)


def _write_evidence_stats(
    path: Path,
    patterns: dict,
    *,
    coverage: dict | None = None,
) -> None:
    """Write a Sprint-6 evidence_stats.json fixture."""
    payload = {
        "schema_version": "2.0",
        "win_delta_threshold": 0.05,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "adjusted_promote_threshold": 0.03,
        "adjusted_demote_threshold": -0.03,
        "coverage": coverage
        or {
            "total": 0, "catalyst_total": 0,
            "catalyst_with_injection_id": 0,
            "catalyst_with_post_score_3": 0,
            "catalyst_with_post_score_5": 0,
            "catalyst_with_code_diff_summary": 0,
            "violations": [],
        },
        "patterns": patterns,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _v2_stat(
    pid: str,
    *,
    placebo_adj: float | None,
    true_n: int = MIN_SAMPLE_SIZE,
    avg_uplift_5: float = 0.05,
    win_rate: float = 0.5,
) -> dict:
    """Build a serialised EvidenceStat dict for fixtures."""
    arm_true = {
        "arm": "true", "n": true_n,
        "avg_uplift_1": None, "avg_uplift_3": None,
        "avg_uplift_5": avg_uplift_5,
        "win_rate": win_rate,
        "regression_rate": 0.0,
        "validity_preservation_rate": 1.0,
    }
    arm_placebo = {
        "arm": "placebo", "n": true_n,
        "avg_uplift_1": None, "avg_uplift_3": None,
        "avg_uplift_5": 0.0,
        "win_rate": 0.0,
        "regression_rate": 0.0,
        "validity_preservation_rate": 1.0,
    }
    arm_zero = {
        "arm": "baseline", "n": 0,
        "avg_uplift_1": None, "avg_uplift_3": None,
        "avg_uplift_5": None, "win_rate": 0.0,
        "regression_rate": 0.0, "validity_preservation_rate": 0.0,
    }
    return {
        "pattern_id": pid,
        "n": true_n * 2,
        "n_by_arm": {"baseline": 0, "true": true_n, "placebo": true_n, "shuffled": 0},
        "by_arm": {
            "baseline": {**arm_zero, "arm": "baseline"},
            "true": arm_true,
            "placebo": arm_placebo,
            "shuffled": {**arm_zero, "arm": "shuffled"},
        },
        "hint_use_rate": 0.8,
        "placebo_adjusted_uplift": placebo_adj,
        "shuffled_adjusted_uplift": None,
        "raw_uplift_mean": avg_uplift_5,
        "last_seen": "2026-06-03T00:00:00Z",
        "is_promoted": False,
        "is_demoted": False,
    }


class ReweightV2Test(unittest.TestCase):
    """Sprint 6 plan §11.8: bridge_reweighter must consult adjusted uplift."""

    def test_promote_when_adjusted_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            stats_path = Path(td) / "evidence_stats.json"
            _write_bridge(
                bridge,
                {
                    "windup": {
                        "standard_name": "PID Windup",
                        "domain": "Control_Locomotion",
                        "associated_patterns": ["compiled_anti_windup"],
                    }
                },
            )
            _write_metrics(metrics, {})
            _write_evidence_stats(
                stats_path,
                {
                    "compiled_anti_windup": _v2_stat(
                        "compiled_anti_windup",
                        placebo_adj=0.15,  # well above +0.03
                        avg_uplift_5=0.15,
                        win_rate=0.9,
                    )
                },
            )
            stats = reweight_bridge_index(
                bridge_path=bridge,
                metrics_path=metrics,
                evidence_stats_path=stats_path,
            )
            self.assertEqual(stats["mode"], "v2")
            self.assertEqual(stats["clusters_promoted"], 1)
            self.assertEqual(stats["clusters_demoted"], 0)
            body = _read_bridge(bridge)
            cluster = body["symptom_clusters"]["windup"]
            self.assertEqual(cluster["priority"], 1)
            self.assertAlmostEqual(cluster["placebo_adjusted_uplift"], 0.15, places=3)

    def test_demote_when_adjusted_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            stats_path = Path(td) / "evidence_stats.json"
            _write_bridge(
                bridge,
                {
                    "windup": {
                        "standard_name": "Windup",
                        "domain": "Control_Locomotion",
                        "associated_patterns": ["compiled_bad_pattern"],
                    }
                },
            )
            _write_metrics(metrics, {})
            _write_evidence_stats(
                stats_path,
                {
                    "compiled_bad_pattern": _v2_stat(
                        "compiled_bad_pattern",
                        placebo_adj=-0.10,
                        avg_uplift_5=-0.10,
                        win_rate=0.0,
                    )
                },
            )
            stats = reweight_bridge_index(
                bridge_path=bridge,
                metrics_path=metrics,
                evidence_stats_path=stats_path,
            )
            self.assertEqual(stats["mode"], "v2")
            self.assertEqual(stats["clusters_demoted"], 1)
            self.assertEqual(_read_bridge(bridge)["symptom_clusters"]["windup"]["priority"], -1)

    def test_hold_when_adjusted_inconclusive(self) -> None:
        """placebo_adj in (-0.03, +0.03) → no promote, no demote."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            stats_path = Path(td) / "evidence_stats.json"
            _write_bridge(
                bridge,
                {
                    "noisy": {
                        "standard_name": "Inconclusive",
                        "domain": "Systems_Compute",
                        "associated_patterns": ["compiled_meh"],
                    }
                },
            )
            _write_metrics(metrics, {})
            _write_evidence_stats(
                stats_path,
                {
                    "compiled_meh": _v2_stat(
                        "compiled_meh", placebo_adj=0.01, avg_uplift_5=0.01,
                    )
                },
            )
            stats = reweight_bridge_index(
                bridge_path=bridge,
                metrics_path=metrics,
                evidence_stats_path=stats_path,
            )
            self.assertEqual(stats["mode"], "v2")
            self.assertEqual(stats["clusters_promoted"], 0)
            self.assertEqual(stats["clusters_demoted"], 0)
            cluster = _read_bridge(bridge)["symptom_clusters"]["noisy"]
            self.assertNotIn("priority", cluster)

    def test_v2_falls_back_to_v1_for_unknown_patterns(self) -> None:
        """A cluster whose patterns aren't in evidence_stats.json should
        still use the v1 demote path (covers partial v2 rollout)."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            stats_path = Path(td) / "evidence_stats.json"
            _write_bridge(
                bridge,
                {
                    "legacy": {
                        "standard_name": "Legacy",
                        "domain": "Memory_Reasoning",
                        "associated_patterns": ["pattern_legacy_v1_only"],
                    }
                },
            )
            _write_metrics(
                metrics,
                {
                    "pattern_legacy_v1_only": _metric(
                        "pattern_legacy_v1_only", n=10, uplift=-0.10, win_rate=0.0,
                    )
                },
            )
            _write_evidence_stats(stats_path, {})  # empty stats
            stats = reweight_bridge_index(
                bridge_path=bridge,
                metrics_path=metrics,
                evidence_stats_path=stats_path,
            )
            self.assertEqual(stats["mode"], "v2")
            self.assertEqual(stats["clusters_demoted"], 1)

    def test_force_v1_ignores_evidence_stats(self) -> None:
        """force_v1=True must skip the v2 path even when stats are present."""
        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "bridge.json"
            metrics = Path(td) / "metrics.json"
            stats_path = Path(td) / "evidence_stats.json"
            _write_bridge(
                bridge,
                {
                    "cluster": {
                        "standard_name": "x",
                        "domain": "Systems_Compute",
                        "associated_patterns": ["compiled_x"],
                    }
                },
            )
            _write_metrics(metrics, {})
            _write_evidence_stats(
                stats_path,
                {
                    "compiled_x": _v2_stat(
                        "compiled_x", placebo_adj=0.15, avg_uplift_5=0.15,
                    )
                },
            )
            stats = reweight_bridge_index(
                bridge_path=bridge,
                metrics_path=metrics,
                evidence_stats_path=stats_path,
                force_v1=True,
            )
            self.assertEqual(stats["mode"], "v1")
            self.assertEqual(stats["clusters_promoted"], 0)


if __name__ == "__main__":
    unittest.main()
