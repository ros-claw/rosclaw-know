"""Tests for Phase 7 active learning autodraft."""
from __future__ import annotations

import asyncio
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

from rosclaw_know.active_learning import (  # noqa: E402
    autodraft_for_blind_spots,
)


def _spot(prefix_hash: str, count: int, samples: list[str] | None = None) -> dict:
    return {
        "prefix_hash": prefix_hash,
        "count": count,
        "samples": samples or [],
        "related_existing_clusters": [],
    }


class AutoDraftTest(unittest.TestCase):
    def test_drafts_high_frequency_spots(self) -> None:
        spots = [
            _spot("hash_a", 10, samples=["err A1", "err A2"]),
            _spot("hash_b", 7, samples=["err B1"]),
            _spot("hash_c", 2),  # below threshold
        ]
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            out = asyncio.run(
                autodraft_for_blind_spots(blind_spots=spots, out_dir=tdp, max_drafts=5)
            )

        # Only spots above threshold (≥5) get drafted; spot_c is excluded by the caller
        # so we still pass it through here — autodraft itself doesn't filter (the
        # HTTP fetcher does). But the threshold is enforced in fetch_blind_spots.
        # Since we pass in raw, all 3 spots are processed; mock LLM draws stub bodies.
        self.assertEqual(len(out), 3)
        names = {p.name for p in out}
        # Each draft filename embeds a sha1 of the prefix_hash
        self.assertTrue(any("md" in n for n in names))

    def test_returns_empty_on_no_spots(self) -> None:
        out = asyncio.run(autodraft_for_blind_spots(blind_spots=[], max_drafts=5))
        self.assertEqual(out, [])

    def test_max_drafts_caps_output(self) -> None:
        spots = [_spot(f"h{i}", 10) for i in range(10)]
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            out = asyncio.run(
                autodraft_for_blind_spots(blind_spots=spots, out_dir=tdp, max_drafts=3)
            )
        self.assertEqual(len(out), 3)

    def test_draft_contains_frontmatter(self) -> None:
        spots = [_spot("test_hash", 8)]
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            paths = asyncio.run(
                autodraft_for_blind_spots(blind_spots=spots, out_dir=tdp, max_drafts=5)
            )
            body = paths[0].read_text(encoding="utf-8")
        self.assertIn("---", body)
        self.assertIn("autodrafted_from: test_hash", body)
        self.assertIn("priority: 0", body)


class FetchBlindSpotsTest(unittest.TestCase):
    """Sanity-check the HTTP-shape adapter (dict / list payload tolerance)."""

    def test_list_payload(self) -> None:
        from rosclaw_know.active_learning import fetch_blind_spots
        # Mock the urlopen to return JSON list
        list_payload = [
            {"prefix_hash": "h1", "count": 6, "samples": ["e1"]},
            {"prefix_hash": "h2", "count": 3},  # below threshold, filtered
        ]
        fake_resp = mock.MagicMock()
        fake_resp.__enter__ = mock.MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = mock.MagicMock(return_value=False)
        fake_resp.read = mock.MagicMock(return_value=json.dumps(list_payload).encode())
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            spots = fetch_blind_spots(url="http://test.local/blind_spots")
        ids = {s["prefix_hash"] for s in spots}
        self.assertEqual(ids, {"h1"})  # h2 below MIN_SAMPLE_THRESHOLD=5

    def test_unreachable_returns_empty(self) -> None:
        from rosclaw_know.active_learning import fetch_blind_spots
        # Unreachable URL → URLError → empty list
        spots = fetch_blind_spots(url="http://127.0.0.1:1/nope")
        self.assertEqual(spots, [])


if __name__ == "__main__":
    unittest.main()
