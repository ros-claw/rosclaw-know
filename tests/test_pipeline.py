"""Smoke tests for the rosclaw-know pipeline plumbing.

Runs without any real LLM (uses ROSCLAW_KNOW_MOCK_LLM=1) so it can run in
CI on a fresh box with no API key.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


# Set mock mode BEFORE importing rosclaw_know so config.MOCK_LLM picks it up.
os.environ["ROSCLAW_KNOW_MOCK_LLM"] = "1"
os.environ.setdefault("DEEPSEEK_API_KEY", "")

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


class PipelineSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        # Re-point all data paths into a temp dir for this test.
        self.tmp = Path(tempfile.mkdtemp(prefix="rosclaw_know_test_"))
        # Build a tiny wiki with two pages from different domains.
        wiki = self.tmp / "wiki"
        wiki.mkdir()
        (wiki / "control_page.md").write_text(
            "# PID Torque Runaway\n\n"
            "The motor PID output saturates and the joint oscillates. "
            "Adding an anti-windup clamp on the integral term stops the runaway. "
            "Without the clamp, the integral term accumulates indefinitely and "
            "saturates the actuator output, leading to oscillation when the load "
            "changes direction. Tuning Kp without limiting integral makes the "
            "behaviour worse, not better.\n"
            "```python\ndef pid_step(err):\n    pass\n```\n",
            encoding="utf-8",
        )
        (wiki / "vision_page.md").write_text(
            "# CUDA OOM in Vision-Language Navigation\n\n"
            "KV-cache memory grows linearly during long trajectories and causes "
            "CUDA OOM. Truncating the cache with a sliding window resolves it. "
            "Keeping the last N timesteps yields almost-identical task accuracy "
            "while bounding GPU memory. Naive solutions like increasing the GPU "
            "memory pool only delay the failure to the next deployment with a "
            "longer trajectory.\n",
            encoding="utf-8",
        )
        (wiki / "fluid_page.md").write_text(
            "# Aerodynamic mesh solver fails to converge\n\n"
            "The CFD solver diverges when the time step is fixed. Adapting the "
            "Courant-Friedrichs-Lewy step based on local velocity stabilises it. "
            "Local cells with high velocity require a smaller step to remain in "
            "the stability region of the explicit integrator. Lowering the global "
            "dt blindly wastes compute on slow regions; raising it triggers blow-up.\n",
            encoding="utf-8",
        )

        # Re-import config with patched paths
        import rosclaw_know.config as cfg

        cfg.DATA_DIR = self.tmp / "data"
        cfg.ASSETS_DIR = cfg.DATA_DIR / "assets"
        cfg.CODE_PATTERNS_DIR = cfg.ASSETS_DIR / "code_patterns"
        cfg.BENCHMARKS_DIR = cfg.DATA_DIR / "benchmarks"
        cfg.DB_PATH = cfg.DATA_DIR / "rosclaw_knowledge.db"
        cfg.WIKI_DIR = wiki
        cfg.MOCK_LLM = True
        cfg.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_end_to_end_mock(self) -> None:
        import asyncio

        from rosclaw_know.pipeline import run_phase1

        summary = asyncio.run(run_phase1())

        # Harvester should have extracted at least 1 page (mock LLM always returns a symptom).
        self.assertGreaterEqual(summary["harvest"]["extracted"], 2)
        # Graph should have nodes from at least 2 domains.
        self.assertGreaterEqual(summary["graph_nodes"], 2)
        # Muse should have written bridge_index.json + at least one pattern.
        self.assertGreaterEqual(summary["muse"]["clusters"], 1)

        from rosclaw_know.config import ASSETS_DIR

        self.assertTrue((ASSETS_DIR / "bridge_index.json").exists())
        patterns = list((ASSETS_DIR / "code_patterns").glob("pattern_*.md"))
        self.assertGreaterEqual(len(patterns), 1)


if __name__ == "__main__":
    unittest.main()
