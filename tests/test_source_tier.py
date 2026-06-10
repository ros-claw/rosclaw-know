"""Unit tests for the source_tier ladder."""
from __future__ import annotations

import pytest

from rosclaw_know.source_tier import (
    A_CURATED_REVIEWED,
    B_TIER_MIN_EVIDENCE_N,
    B_TRAJECTORY_MINED,
    C_MUSE_SYNTH,
    D_AUTODRAFT,
    F_DEMOTED,
    S_CURATED_VERIFIED,
    SOURCE_TIER_LADDER,
    infer_source_tier,
)


class TestLadderDefinition:
    def test_ladder_is_stable_order(self):
        assert SOURCE_TIER_LADDER == (
            S_CURATED_VERIFIED,
            A_CURATED_REVIEWED,
            B_TRAJECTORY_MINED,
            C_MUSE_SYNTH,
            D_AUTODRAFT,
            F_DEMOTED,
        )

    def test_all_tiers_are_distinct_strings(self):
        assert len(set(SOURCE_TIER_LADDER)) == len(SOURCE_TIER_LADDER)
        for t in SOURCE_TIER_LADDER:
            assert isinstance(t, str) and t.startswith(("S_", "A_", "B_", "C_", "D_", "F_"))


class TestInferSourceTier:
    def test_explicit_tier_wins(self):
        # If a cluster already carries a tier, leave it (idempotent reload).
        c = {"source_tier": A_CURATED_REVIEWED, "source": "muse"}
        assert infer_source_tier(c) == A_CURATED_REVIEWED

    def test_explicit_unknown_tier_falls_through(self):
        # If the tier string isn't in the ladder, treat the cluster as
        # un-tiered and re-infer (defensive against schema drift).
        c = {"source_tier": "Z_UNKNOWN", "source": "curated"}
        assert infer_source_tier(c) == S_CURATED_VERIFIED

    def test_curated_source(self):
        c = {"source": "curated"}
        assert infer_source_tier(c) == S_CURATED_VERIFIED

    def test_demoted_lifecycle(self):
        c = {"metadata": {"lifecycle_status": "demoted"}}
        assert infer_source_tier(c) == F_DEMOTED

    def test_autodrafted_flag(self):
        for md in [
            {"autodrafted": True},
            {"auto_drafted": True},
            {"source": "autodraft"},
        ]:
            c = {"metadata": md}
            assert infer_source_tier(c) == D_AUTODRAFT, md

    def test_trajectory_promotion_threshold(self):
        # Right at the boundary: n=2 + uplift>0 should promote.
        c = {
            "metadata": {
                "evidence": {
                    "n": B_TIER_MIN_EVIDENCE_N,
                    "avg_uplift": 0.01,
                }
            }
        }
        assert infer_source_tier(c) == B_TRAJECTORY_MINED

    def test_trajectory_not_promoted_without_evidence(self):
        # n=1 isn't enough.
        c = {"metadata": {"evidence": {"n": 1, "avg_uplift": 5.0}}}
        assert infer_source_tier(c) == C_MUSE_SYNTH

    def test_trajectory_not_promoted_without_uplift(self):
        # avg_uplift == 0 must not promote (strict >).
        c = {"metadata": {"evidence": {"n": 5, "avg_uplift": 0.0}}}
        assert infer_source_tier(c) == C_MUSE_SYNTH

    def test_trajectory_not_promoted_with_negative_uplift(self):
        # Real run can record negative uplift — don't promote those.
        c = {"metadata": {"evidence": {"n": 5, "avg_uplift": -0.4}}}
        assert infer_source_tier(c) == C_MUSE_SYNTH

    def test_default_muse_synth(self):
        # Plain Muse output: no autodraft flag, no evidence yet.
        c = {
            "metadata": {
                "source_quality": "C",
                "lifecycle_status": "needs_validation",
                "evidence": {"n": 0, "avg_uplift": 0.0},
            }
        }
        assert infer_source_tier(c) == C_MUSE_SYNTH

    def test_missing_metadata(self):
        # Defensive: no metadata at all → still synth (most-conservative default).
        assert infer_source_tier({}) == C_MUSE_SYNTH

    def test_garbage_evidence_does_not_crash(self):
        # Malformed evidence shouldn't blow the publisher up.
        for ev in [
            {"n": "abc", "avg_uplift": 0.5},
            {"n": None, "avg_uplift": "huh"},
            None,
            {},
        ]:
            c = {"metadata": {"evidence": ev}}
            assert infer_source_tier(c) == C_MUSE_SYNTH

    def test_precedence_demoted_beats_autodraft(self):
        # If both signals fire, demotion wins (more specific decision).
        c = {
            "metadata": {
                "lifecycle_status": "demoted",
                "autodrafted": True,
                "evidence": {"n": 10, "avg_uplift": 1.0},
            }
        }
        assert infer_source_tier(c) == F_DEMOTED

    def test_precedence_autodraft_beats_trajectory(self):
        # Both autodraft signal AND positive trajectory evidence —
        # autodraft takes precedence (it's a stronger provenance claim).
        c = {
            "metadata": {
                "autodrafted": True,
                "evidence": {"n": 10, "avg_uplift": 1.0},
            }
        }
        assert infer_source_tier(c) == D_AUTODRAFT
