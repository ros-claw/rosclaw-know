"""Tests for ``scripts/lint_bridge.py`` detection helpers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("ROSCLAW_KNOW_MOCK_LLM", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for p in (SRC, SCRIPTS):
    sys.path.insert(0, str(p))

from lint_bridge import (  # type: ignore[import-not-found]  # noqa: E402
    find_duplicate_names,
    find_missing_pattern_files,
    find_orphan_patterns,
    find_stale_demotions,
    lint,
)


def _patterns_dir(tdp: Path, names: list[str]) -> Path:
    d = tdp / "code_patterns"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("# pattern", encoding="utf-8")
    return d


def _bridge(clusters: dict) -> dict:
    return {"symptom_clusters": clusters}


class OrphanTest(unittest.TestCase):
    def test_no_orphans_when_all_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["anti_windup_pid.md", "pattern_kv_cache.md"])
            bridge = _bridge({
                "c1": {"associated_patterns": ["anti_windup_pid"]},
                "c2": {"associated_patterns": ["pattern_kv_cache"]},
            })
            self.assertEqual(find_orphan_patterns(bridge, pdir), [])

    def test_orphan_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["anti_windup_pid.md", "lost_one.md"])
            bridge = _bridge({"c1": {"associated_patterns": ["anti_windup_pid"]}})
            orphans = find_orphan_patterns(bridge, pdir)
            self.assertEqual(orphans, ["lost_one.md"])

    def test_prefixed_referenced_form_matches(self) -> None:
        """A cluster referring to 'pattern_x' should not orphan 'x.md'."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["x.md"])
            bridge = _bridge({"c1": {"associated_patterns": ["pattern_x"]}})
            self.assertEqual(find_orphan_patterns(bridge, pdir), [])


class MissingTest(unittest.TestCase):
    def test_missing_file_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["x.md"])
            bridge = _bridge({
                "c1": {"associated_patterns": ["x"]},
                "c2": {"associated_patterns": ["y"]},  # y.md doesn't exist
            })
            missing = find_missing_pattern_files(bridge, pdir)
            self.assertIn(("c2", "y"), missing)
            self.assertNotIn(("c1", "x"), missing)

    def test_prefixed_form_lookup(self) -> None:
        """A cluster referencing 'pattern_x' should find 'x.md' on disk."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["x.md"])
            bridge = _bridge({"c1": {"associated_patterns": ["pattern_x"]}})
            self.assertEqual(find_missing_pattern_files(bridge, pdir), [])


class DuplicateNamesTest(unittest.TestCase):
    def test_duplicates_grouped(self) -> None:
        bridge = _bridge({
            "c1": {"standard_name": "PID wind-up"},
            "c2": {"standard_name": "PID wind-up"},
            "c3": {"standard_name": "KV cache OOM"},
        })
        dups = find_duplicate_names(bridge)
        self.assertIn("PID wind-up", dups)
        self.assertCountEqual(dups["PID wind-up"], ["c1", "c2"])
        self.assertNotIn("KV cache OOM", dups)

    def test_empty_names_ignored(self) -> None:
        bridge = _bridge({
            "c1": {"standard_name": ""},
            "c2": {"standard_name": ""},
        })
        self.assertEqual(find_duplicate_names(bridge), {})


class StaleDemotionsTest(unittest.TestCase):
    def test_stale_old_demotion_flagged(self) -> None:
        now = datetime(2026, 5, 17, tzinfo=UTC)
        stale_iso = (now - timedelta(days=45)).isoformat()
        bridge = _bridge({
            "old_loser": {"priority": -1, "last_seen": stale_iso},
            "young_loser": {"priority": -1, "last_seen": now.isoformat()},
            "active_pattern": {"priority": 0},
        })
        stale = find_stale_demotions(bridge, stale_days=30, now=now)
        ids = {cid for cid, _ in stale}
        self.assertIn("old_loser", ids)
        self.assertNotIn("young_loser", ids)
        self.assertNotIn("active_pattern", ids)

    def test_missing_timestamp_flagged(self) -> None:
        now = datetime(2026, 5, 17, tzinfo=UTC)
        bridge = _bridge({"no_ts": {"priority": -1}})
        stale = find_stale_demotions(bridge, stale_days=30, now=now)
        self.assertEqual([cid for cid, _ in stale], ["no_ts"])

    def test_z_suffix_iso_parses(self) -> None:
        now = datetime(2026, 5, 17, tzinfo=UTC)
        bridge = _bridge({
            "z_format": {
                "priority": -1,
                "last_seen": (now - timedelta(days=60)).isoformat().replace("+00:00", "Z"),
            }
        })
        stale = find_stale_demotions(bridge, stale_days=30, now=now)
        self.assertEqual(len(stale), 1)


class LintEnvelopeTest(unittest.TestCase):
    def test_clean_returns_zero_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["anti_windup_pid.md"])
            bpath = tdp / "bridge.json"
            bpath.write_text(json.dumps(_bridge({
                "c1": {
                    "standard_name": "PID",
                    "associated_patterns": ["anti_windup_pid"],
                }
            })), encoding="utf-8")
            report = lint(bpath, pdir, stale_days=30)
            self.assertEqual(report["anomaly_count"], 0)
            self.assertEqual(report["cluster_count"], 1)

    def test_dirty_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            pdir = _patterns_dir(tdp, ["lonely.md"])
            bpath = tdp / "bridge.json"
            bpath.write_text(json.dumps(_bridge({
                "c1": {"standard_name": "A", "associated_patterns": ["missing_one"]},
                "c2": {"standard_name": "A"},
            })), encoding="utf-8")
            report = lint(bpath, pdir, stale_days=30)
            self.assertGreater(report["anomaly_count"], 0)
            self.assertIn("missing_pattern_files", report)
            self.assertIn("lonely.md", report["orphan_patterns"])


if __name__ == "__main__":
    unittest.main()
