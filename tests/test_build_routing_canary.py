"""Tests for the routing-canary generator (scripts/build_routing_canary.py).

The generator is a pure read of CURATED_SAFETY_PATTERNS → produce a fixed
schema JSON spec consumed by rosclaw-how's reload self-probe. Tests
focus on: query construction, sibling expansion (anti_windup_pid +
motion_blur), schema completeness.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_routing_canary",
        Path(__file__).resolve().parent.parent / "scripts" / "build_routing_canary.py",
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def brc():
    return _load_module()


class TestQueryConstruction:
    def test_query_uses_safety_label_and_first_6_keywords(self, brc):
        p = SimpleNamespace(
            pattern_id="x",
            standard_name="Some Standard Name",
            safety_label="Memory_Exhaustion",
            domain="LLM",
            matched_keywords=[
                "kv cache", "long context", "attention", "memory",
                "evict", "sliding", "window", "buffer",
            ],
        )
        q = brc._make_query(p)
        # safety label tokens lowered + underscore→space
        assert "memory exhaustion" in q
        # first 6 keywords only
        assert "kv cache" in q and "long context" in q and "sliding" in q
        # 7th+ keywords excluded
        assert "window" not in q
        assert "buffer" not in q

    def test_query_no_standard_name_leakage(self, brc):
        """Querying the standard_name would round-trip the embedding to sim≈1
        and mask drift. Make sure the generator never includes it."""
        p = SimpleNamespace(
            pattern_id="x",
            standard_name="A very specific standard name that should NOT appear",
            safety_label="X",
            domain="d",
            matched_keywords=["kw1", "kw2"],
        )
        q = brc._make_query(p)
        assert "specific standard name" not in q

    def test_query_lowercased(self, brc):
        p = SimpleNamespace(
            pattern_id="x",
            standard_name="n",
            safety_label="UPPER_CASE_LABEL",
            domain="d",
            matched_keywords=["MixedCase", "ALLCAPS"],
        )
        q = brc._make_query(p)
        assert q == q.lower()


class TestSiblings:
    def test_anti_windup_pid_pair(self, brc):
        assert "anti_windup_pid" in brc._SIBLINGS
        assert brc._SIBLINGS["anti_windup_pid"] == {
            "anti_windup_pid",
            "output_saturation_clamp",
        }

    def test_pair_is_bidirectional_for_curated_pairs(self, brc):
        """Both members of a curated↔curated sibling pair must list the other.

        Curated↔synth pairs (e.g. motion_blur_imu_aided_deblur ↔
        motion_blur_decomposition_with_cross-shutter_guidance) are
        asymmetric on purpose: only the curated has a canary, so only
        the curated key needs the synth in its sibling set.
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS

        curated_ids = {p.pattern_id for p in CURATED_SAFETY_PATTERNS}
        for a, group in list(brc._SIBLINGS.items()):
            for b in group:
                if a != b and b in curated_ids:
                    assert b in brc._SIBLINGS, f"{b} not in _SIBLINGS"
                    assert a in brc._SIBLINGS[b], f"{a} missing from {b}'s siblings"

    def test_motion_blur_synth_accepted(self, brc):
        # The synth motion_blur cluster outranks the curated by sim margin
        # ~0.05; HOW's curated_preference rescue is the live contract.
        # Canary must accept the synth as a top-1 sibling.
        assert "motion_blur_imu_aided_deblur" in brc._SIBLINGS
        assert (
            "motion_blur_decomposition_with_cross-shutter_guidance"
            in brc._SIBLINGS["motion_blur_imu_aided_deblur"]
        )


class TestMainEndToEnd:
    def test_emits_one_canary_per_curated(self, brc, tmp_path, monkeypatch):
        # Redirect ASSETS_DIR so we don't clobber live data.
        out_dir = tmp_path / "assets"
        out_dir.mkdir()
        monkeypatch.setattr(brc.config, "ASSETS_DIR", out_dir)

        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS

        rc = brc.main()
        assert rc == 0

        out_path = out_dir / "routing_canary.json"
        assert out_path.exists()
        spec = json.loads(out_path.read_text(encoding="utf-8"))
        assert spec["schema_version"] == 1
        assert spec["curated_count"] == len(CURATED_SAFETY_PATTERNS)
        assert len(spec["canaries"]) == len(CURATED_SAFETY_PATTERNS)
        assert spec["default_min_similarity"] == 0.55

        # Every canary entry has the required fields
        for c in spec["canaries"]:
            assert c["name"]
            assert c["query"]
            assert isinstance(c["expected_top1_any"], list)
            assert c["expected_top1_any"], "expected_top1_any must be non-empty"
            assert c["min_similarity"] == 0.55
            assert "domain" in c
            assert "safety_label" in c

    def test_anti_windup_canary_has_both_siblings(self, brc, tmp_path, monkeypatch):
        out_dir = tmp_path / "assets"
        out_dir.mkdir()
        monkeypatch.setattr(brc.config, "ASSETS_DIR", out_dir)
        brc.main()
        spec = json.loads((out_dir / "routing_canary.json").read_text(encoding="utf-8"))
        # Find the anti_windup canary and confirm sibling expansion
        awp = [c for c in spec["canaries"] if c["name"] == "anti_windup_pid"]
        assert awp, "anti_windup_pid canary missing"
        exp = set(awp[0]["expected_top1_any"])
        assert exp == {"anti_windup_pid", "output_saturation_clamp"}

    def test_motion_blur_canary_has_synth_sibling(self, brc, tmp_path, monkeypatch):
        out_dir = tmp_path / "assets"
        out_dir.mkdir()
        monkeypatch.setattr(brc.config, "ASSETS_DIR", out_dir)
        brc.main()
        spec = json.loads((out_dir / "routing_canary.json").read_text(encoding="utf-8"))
        mb = [c for c in spec["canaries"] if c["name"] == "motion_blur_imu_aided_deblur"]
        assert mb
        exp = set(mb[0]["expected_top1_any"])
        assert "motion_blur_imu_aided_deblur" in exp
        assert "motion_blur_decomposition_with_cross-shutter_guidance" in exp
