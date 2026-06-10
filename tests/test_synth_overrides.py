"""Tests for synth_overrides surgical demotion mechanism."""
from __future__ import annotations

import pytest

from rosclaw_know.source_tier import (
    C_MUSE_SYNTH,
    F_DEMOTED,
    S_CURATED_VERIFIED,
)
from rosclaw_know.synth_overrides import (
    SYNTH_DEMOTIONS,
    apply_synth_overrides,
    infer_source_tier_with_overrides,
)


class TestSynthDemotionsSchema:
    def test_pid_antiwindup_listed(self):
        """The T_001 fix lives here; if it disappears, surface a clear failure."""
        assert "pid_antiwindup" in SYNTH_DEMOTIONS

    def test_every_demotion_has_a_reason(self):
        """Each demotion is policy; the reason string is the audit trail."""
        for cid, reason in SYNTH_DEMOTIONS.items():
            assert isinstance(reason, str)
            assert len(reason) > 50, f"{cid} reason too short ({len(reason)} chars)"

    def test_pid_antiwindup_reason_mentions_curated(self):
        r = SYNTH_DEMOTIONS["pid_antiwindup"]
        assert "anti_windup_pid" in r or "anti-windup" in r.lower()


class TestApplyOverrides:
    def test_demoted_synth_returns_F(self):
        c = {"source": "muse", "metadata": {"evidence": {"n": 0}}}
        assert apply_synth_overrides(c, "pid_antiwindup") == F_DEMOTED

    def test_demoted_synth_also_mutates_lifecycle_status(self):
        """HOW's existing demoted-skip path reads metadata.lifecycle_status,
        NOT source_tier. apply_synth_overrides MUST mutate the metadata so
        HOW actually drops the cluster at runtime."""
        c = {"source": "muse", "metadata": {}}
        apply_synth_overrides(c, "pid_antiwindup")
        assert c["metadata"]["lifecycle_status"] == "demoted"
        assert (
            c["metadata"].get("lifecycle_status_reason")
            == "synth_overrides_demotion"
        )

    def test_demoted_synth_also_sets_priority_negative(self):
        """The PRIMARY signal HOW reads is cluster.priority < 0.
        Setting source_tier=F_DEMOTED without priority < 0 is cosmetic only.
        """
        c = {"source": "muse"}
        apply_synth_overrides(c, "pid_antiwindup")
        assert c.get("priority") == -1, (
            "priority must be -1 so HOW's asset_loader treats the cluster as demoted"
        )

    def test_demotion_creates_metadata_if_missing(self):
        c = {"source": "muse"}  # no metadata key at all
        apply_synth_overrides(c, "pid_antiwindup")
        assert c["metadata"]["lifecycle_status"] == "demoted"

    def test_demotion_idempotent_on_already_demoted(self):
        c = {
            "source": "muse",
            "metadata": {
                "lifecycle_status": "demoted",
                "lifecycle_status_reason": "muse_extractor_demoted_in_pass_3",
            },
        }
        apply_synth_overrides(c, "pid_antiwindup")
        # Don't clobber an existing demotion reason — preserve provenance.
        assert (
            c["metadata"]["lifecycle_status_reason"]
            == "muse_extractor_demoted_in_pass_3"
        )

    def test_unlisted_synth_does_not_mutate(self):
        c = {"source": "muse"}
        apply_synth_overrides(c, "some_other_synth")
        # The function returned None; cluster must be untouched.
        assert "metadata" not in c or "lifecycle_status" not in (c.get("metadata") or {})

    def test_unlisted_synth_returns_none(self):
        c = {"source": "muse"}
        assert apply_synth_overrides(c, "some_other_synth") is None

    def test_curated_never_overridden(self):
        """Even if a curated cluster_id is wrongly listed in SYNTH_DEMOTIONS,
        the curated must keep its S_CURATED_VERIFIED tier — never demote."""
        c = {"source": "curated"}
        # Deliberately use a known curated id to confirm the curated guard
        assert apply_synth_overrides(c, "anti_windup_pid") is None
        # AND no metadata mutation either
        assert "metadata" not in c or "lifecycle_status" not in (c.get("metadata") or {})


class TestInferWithOverrides:
    def test_demoted_synth_wins(self):
        c = {
            "source": "muse",
            "metadata": {
                "evidence": {"n": 5, "avg_uplift": 1.0},  # would be B_TRAJECTORY_MINED
                "lifecycle_status": "needs_validation",
            },
        }
        # Even though evidence would normally promote to B, the override demotes.
        assert infer_source_tier_with_overrides(c, "pid_antiwindup") == F_DEMOTED

    def test_unlisted_synth_falls_through(self):
        c = {
            "source": "muse",
            "metadata": {
                "evidence": {"n": 5, "avg_uplift": 1.0},
                "lifecycle_status": "needs_validation",
            },
        }
        # Not in SYNTH_DEMOTIONS → normal inference → B_TRAJECTORY_MINED
        from rosclaw_know.source_tier import B_TRAJECTORY_MINED
        assert infer_source_tier_with_overrides(c, "high_level_planner") == B_TRAJECTORY_MINED

    def test_curated_stays_S(self):
        c = {"source": "curated"}
        # The curated check happens before SYNTH_DEMOTIONS consultation
        assert (
            infer_source_tier_with_overrides(c, "anti_windup_pid")
            == S_CURATED_VERIFIED
        )

    def test_unlisted_synth_with_no_evidence_is_C(self):
        c = {"source": "muse"}
        assert (
            infer_source_tier_with_overrides(c, "random_synth_id")
            == C_MUSE_SYNTH
        )


class TestLiveRegistryGuard:
    """Tests that lock in iter4_p2 invariants on the actual bridge."""

    def test_pid_antiwindup_demoted_in_live_bridge(self, tmp_path, monkeypatch):
        """After republishing, pid_antiwindup MUST end up at F_DEMOTED.

        This is the iter4_p2 contract. If the publisher accidentally drops
        the override wiring or curated_publisher imports the wrong fn,
        this test fails immediately.
        """
        import json
        from pathlib import Path

        # Use the LIVE bridge (post-iter4_p2 republish).
        bridge_path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "assets"
            / "bridge_index.json"
        )
        if not bridge_path.exists():
            pytest.skip(
                "data/assets/bridge_index.json not present; this test runs only "
                "against a freshly-published bridge."
            )
        data = json.loads(bridge_path.read_text(encoding="utf-8"))
        clusters = data.get("symptom_clusters", {})
        if "pid_antiwindup" not in clusters:
            pytest.skip(
                "pid_antiwindup absent from bridge — synth_overrides irrelevant."
            )
        actual_tier = clusters["pid_antiwindup"].get("source_tier")
        assert actual_tier == F_DEMOTED, (
            f"pid_antiwindup must be F_DEMOTED post-iter4_p2; got {actual_tier!r}. "
            "Either the publisher dropped the override wiring or the bridge "
            "wasn't re-published after editing SYNTH_DEMOTIONS."
        )
