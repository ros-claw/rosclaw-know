"""Tests for the Phase 5 incremental ingest pipeline.

Avoids the LLM and harvester — those have their own tests. We exercise:

  * ``merge_into_bridge`` non-destructive merge semantics
  * Path gathering ignores non-markdown
  * Bridge merge preserves existing fields when adding new clusters
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("ROSCLAW_KNOW_MOCK_LLM", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


class MergeIntoBridgeTest(unittest.TestCase):
    """Verify Phase 4 fields survive Phase 5 cluster merges."""

    def _setup_tmp_assets(self, tmp_dir: Path, existing: dict | None = None) -> Path:
        assets = tmp_dir / "assets"
        assets.mkdir()
        (assets / "code_patterns").mkdir()
        bridge_path = assets / "bridge_index.json"
        if existing:
            bridge_path.write_text(json.dumps(existing), encoding="utf-8")
        return assets

    def test_new_clusters_added_existing_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            assets = self._setup_tmp_assets(tdp, existing={
                "symptom_clusters": {
                    "anti_windup_pid": {
                        "standard_name": "Existing cluster",
                        "domain": "Control_Locomotion",
                        "associated_patterns": ["anti_windup_pid"],
                        "uplift_mean": 0.18,
                        "uplift_n": 12,
                        "win_rate": 0.83,
                        # Phase 4 priority should survive merge
                    },
                },
                "safety_label_index": {"Torque_Overflow": "anti_windup_pid"},
            })
            with mock.patch("rosclaw_know.incremental_pipeline.config") as cfg_mock:
                cfg_mock.ASSETS_DIR = assets
                cfg_mock.CODE_PATTERNS_DIR = assets / "code_patterns"
                from rosclaw_know.incremental_pipeline import merge_into_bridge

                stats = merge_into_bridge({
                    "tpu_oom_fragment": {
                        "standard_name": "TPU memory fragmentation during XLA compile",
                        "domain": "Systems_Compute",
                        "associated_patterns": ["pattern_tpu_oom_fragment"],
                        "cross_domain_analogies": [],
                        "matched_keywords": ["tpu", "memory", "xla"],
                    },
                })

            self.assertEqual(stats["added"], 1)
            self.assertEqual(stats["skipped_existing"], 0)
            self.assertEqual(stats["total"], 2)

            payload = json.loads((assets / "bridge_index.json").read_text(encoding="utf-8"))
            # New cluster is in
            self.assertIn("tpu_oom_fragment", payload["symptom_clusters"])
            # Existing cluster's Phase-4 fields untouched
            anti = payload["symptom_clusters"]["anti_windup_pid"]
            self.assertEqual(anti["uplift_mean"], 0.18)
            self.assertEqual(anti["uplift_n"], 12)
            self.assertEqual(anti["win_rate"], 0.83)
            # Top-level safety_label_index also preserved
            self.assertEqual(
                payload["safety_label_index"], {"Torque_Overflow": "anti_windup_pid"}
            )

    def test_duplicate_id_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            assets = self._setup_tmp_assets(tdp, existing={
                "symptom_clusters": {
                    "n1": {"standard_name": "old name"},
                },
            })
            with mock.patch("rosclaw_know.incremental_pipeline.config") as cfg_mock:
                cfg_mock.ASSETS_DIR = assets
                cfg_mock.CODE_PATTERNS_DIR = assets / "code_patterns"
                from rosclaw_know.incremental_pipeline import merge_into_bridge

                stats = merge_into_bridge({
                    "n1": {"standard_name": "should not overwrite"},
                })
            self.assertEqual(stats["added"], 0)
            self.assertEqual(stats["skipped_existing"], 1)
            payload = json.loads((assets / "bridge_index.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["symptom_clusters"]["n1"]["standard_name"], "old name")


class GatherCandidatesTest(unittest.TestCase):
    def test_directory_recurses_only_markdown(self) -> None:
        from rosclaw_know.incremental_pipeline import _gather_candidate_paths
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "sub").mkdir()
            (tdp / "a.md").write_text("a", encoding="utf-8")
            (tdp / "b.txt").write_text("b", encoding="utf-8")
            (tdp / "sub" / "c.md").write_text("c", encoding="utf-8")
            out = _gather_candidate_paths([tdp])
            names = sorted(p.name for p in out)
            self.assertEqual(names, ["a.md", "c.md"])

    def test_explicit_file_passthrough(self) -> None:
        from rosclaw_know.incremental_pipeline import _gather_candidate_paths
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "x.md").write_text("x", encoding="utf-8")
            out = _gather_candidate_paths([tdp / "x.md"])
            self.assertEqual([p.name for p in out], ["x.md"])

    def test_non_markdown_ignored(self) -> None:
        from rosclaw_know.incremental_pipeline import _gather_candidate_paths
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "x.pdf").write_text("x", encoding="utf-8")
            out = _gather_candidate_paths([tdp / "x.pdf"])
            self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
