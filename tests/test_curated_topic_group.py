"""iter4_p3 invariants on the curated → topic_group assignment.

Why this test exists: without topic_group, HOW's topic-filtered routing
(``topic_filter_path=top1``) silently excludes curated clusters from the
candidate pool whenever the query carries a non-empty topic_group. The
failure mode is invisible — patterns/search still returns the curated by
cosine sim, but /prompt/build's CATALYST path filters it out.

These tests guard against:
1. A future curated added without topic_group (silent reach loss).
2. topic_group set to a string not present in HOW's bridge (orphans the
   curated into a topic group nothing else routes to).
3. The publisher dropping topic_group on its way to bridge_index.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rosclaw_know import curated_publisher
from rosclaw_know.curated_patterns import (
    CURATED_SAFETY_PATTERNS,
    CuratedPattern,
)


# These are the topic_groups currently emitted by Muse's autodraft pass on
# the rosclaw-how bridge (~20 distinct values). Curated patterns MUST land
# in one of these so they join the same topic-filtered candidate pool as
# the synth clusters that compete with them. New groups added by Muse are
# fine; this list is a floor, not a cap — but reusing existing groups
# preserves routing topology.
KNOWN_TOPIC_GROUPS = frozenset(
    {
        "navigation-and-vla",
        "3d-perception-and-mapping",
        "language-to-action-grounding",
        "data-scarcity-and-generalization",
        "rl-training-stability",
        "sim-to-real-transfer",
        "cybersecurity-and-resilience",
        "llm-planning-and-reasoning",
        "simulation-and-numerics",
        "control-loop-stability",
        "locomotion-and-manipulation",
        "llm-inference-efficiency",
        "battery-and-energy-management",
        "modular-pipeline-failures",
        "fault-tolerant-compute",
        "robot-morphology-and-constraints",
        "llm-context-management",
        "scheduling-optimization",
        "software-engineering-and-tooling",
        "multi-robot-and-multi-agent",
        # iter4_p4 (2026-06-10) — single-cluster group for
        # simd_aes_ni_hardware_crypto. Was in cybersecurity-and-resilience
        # but that group's 19-cluster fingerprint is dominated by 18
        # ICS/SCADA defense entries (Modbus/PLC/S7), whose vocab has zero
        # overlap with crypto-throughput queries (T_005 AES-128 80 MB/s).
        # Live-probe at p3: T_005 admitted=[scheduling-optimization 0.3702,
        # fault-tolerant-compute 0.3687], cybersecurity-and-resilience at
        # rank 5 (0.2652). simd_aes_ni was filtered out of CATALYST's
        # candidate pool entirely. New dedicated group lifts T_005's
        # cluster admission. Acceptable for a 1-cluster group: this is
        # how Muse autodrafts new groups too. See
        # project_iter4_p4_simd_aes_ni_topic_group_move.md.
        "hardware-accelerated-cryptography",
    }
)


class TestCuratedTopicGroupAssignment:
    def test_every_curated_has_topic_group(self):
        """The whole point of iter4_p3 — no silent topic-filter exclusion."""
        missing = [p.pattern_id for p in CURATED_SAFETY_PATTERNS if p.topic_group is None]
        assert not missing, (
            f"{len(missing)} curated pattern(s) without topic_group: {missing}. "
            "Every curated MUST set topic_group so HOW's topic-filter routing admits "
            "it to the candidate pool. See iter4_p3 memory for context."
        )

    def test_topic_groups_are_known(self):
        """Catch typos / drift from the Muse-emitted group vocabulary."""
        unknown: list[tuple[str, str]] = []
        for p in CURATED_SAFETY_PATTERNS:
            tg = p.topic_group
            assert tg is not None  # covered by test above; redundant for clarity
            if tg not in KNOWN_TOPIC_GROUPS:
                unknown.append((p.pattern_id, tg))
        assert not unknown, (
            "Curated pattern(s) assigned to topic_group(s) not present in HOW's "
            "bridge vocabulary; this orphans the curated into an empty pool. "
            f"Offenders: {unknown}. Either fix the typo or add the group to "
            "KNOWN_TOPIC_GROUPS if Muse has started emitting it."
        )

    def test_control_loop_stability_has_curated(self):
        """T_001 PIDTuning's bottleneck — at least one curated must live here.

        Without a curated in control-loop-stability, T_001's topic-filtered
        candidate pool was 11 synth clusters and zero curated, leaving the
        curated reachable only by safety-label exact match or rescue.
        """
        in_group = [
            p.pattern_id
            for p in CURATED_SAFETY_PATTERNS
            if p.topic_group == "control-loop-stability"
        ]
        assert len(in_group) >= 1, (
            "control-loop-stability must contain at least one curated "
            f"(anti_windup_pid / output_saturation_clamp). Found: {in_group}"
        )


class TestCuratedPublisherEmitsTopicGroup:
    def test_cluster_entry_includes_topic_group(self):
        """The bridge writer MUST pass topic_group through."""
        p = CuratedPattern(
            pattern_id="test_pattern",
            safety_label="Test_Label",
            standard_name="Test pattern",
            domain="Memory_Reasoning",
            matched_keywords=["test"],
            fix_pattern="Do the thing.",
            failed_attempt="Don't do the thing.",
            before_code="x = 1\n",
            after_code="x = 2\n",
            cross_domain_hints=[],
            topic_group="control-loop-stability",
        )
        entry = curated_publisher._build_cluster_entry(p)
        assert entry.get("topic_group") == "control-loop-stability"

    def test_cluster_entry_omits_topic_group_when_none(self):
        """Default None → absent field (don't poison the bridge with nulls)."""
        p = CuratedPattern(
            pattern_id="test_no_group",
            safety_label="Test_Label",
            standard_name="Test pattern",
            domain="Memory_Reasoning",
            matched_keywords=["test"],
            fix_pattern="Do the thing.",
            failed_attempt="Don't do the thing.",
            before_code="x = 1\n",
            after_code="x = 2\n",
            cross_domain_hints=[],
            # topic_group defaults to None
        )
        entry = curated_publisher._build_cluster_entry(p)
        assert "topic_group" not in entry

    def test_topic_group_in_routing_critical_fields(self):
        """source_tier and topic_group both belong in ROUTING_CRITICAL_FIELDS
        so content_hash changes when either flips. Without this, a topic_group
        update wouldn't trigger HOW's re-embed-on-hash-change path."""
        assert "topic_group" in curated_publisher.ROUTING_CRITICAL_FIELDS

    def test_cluster_entry_includes_topic_tag_when_set(self):
        """iter4_p4: topic_tag MUST round-trip through publisher.

        Without this, HOW's _build_group_to_fingerprint_text silently
        drops the cluster from its group's fingerprint. The whole point
        of moving simd_aes_ni to a new dedicated topic_group is undone
        unless the tag is emitted too.
        """
        p = CuratedPattern(
            pattern_id="test_pattern_tagged",
            safety_label="Test_Label",
            standard_name="Test pattern with tag",
            domain="Memory_Reasoning",
            matched_keywords=["test"],
            fix_pattern="Do the thing.",
            failed_attempt="Don't do the thing.",
            before_code="x = 1\n",
            after_code="x = 2\n",
            cross_domain_hints=[],
            topic_group="control-loop-stability",
            topic_tag="anti-windup-conditional-integration",
        )
        entry = curated_publisher._build_cluster_entry(p)
        assert entry.get("topic_tag") == "anti-windup-conditional-integration"

    def test_cluster_entry_omits_topic_tag_when_none(self):
        """Default None → absent field (no null pollution)."""
        p = CuratedPattern(
            pattern_id="test_no_tag",
            safety_label="Test_Label",
            standard_name="Test pattern",
            domain="Memory_Reasoning",
            matched_keywords=["test"],
            fix_pattern="Do the thing.",
            failed_attempt="Don't do the thing.",
            before_code="x = 1\n",
            after_code="x = 2\n",
            cross_domain_hints=[],
            # topic_tag defaults to None
        )
        entry = curated_publisher._build_cluster_entry(p)
        assert "topic_tag" not in entry

    def test_topic_tag_in_routing_critical_fields(self):
        """iter4_p4: topic_tag in ROUTING_CRITICAL_FIELDS so a tag change
        flips content_hash and triggers HOW re-embed."""
        assert "topic_tag" in curated_publisher.ROUTING_CRITICAL_FIELDS

    def test_simd_aes_ni_has_topic_tag(self):
        """iter4_p4 invariant: simd_aes_ni MUST carry a topic_tag.

        The cluster sits in a single-cluster topic_group
        ``hardware-accelerated-cryptography``; if topic_tag is None the
        fingerprint is never built (group becomes invisible) and T_005
        routing breaks again.
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        simd = next(
            (p for p in CURATED_SAFETY_PATTERNS if p.pattern_id == "simd_aes_ni_hardware_crypto"),
            None,
        )
        assert simd is not None, "simd_aes_ni_hardware_crypto missing from curated registry"
        assert simd.topic_tag is not None, (
            "simd_aes_ni_hardware_crypto needs topic_tag for its single-cluster "
            "hardware-accelerated-cryptography group fingerprint to build at all"
        )
        assert simd.topic_group == "hardware-accelerated-cryptography"

    def test_iter4_p5_tagged_curated_keep_topic_tag(self):
        """iter4_p5 invariant: the 7 clusters tagged in iter4_p5 MUST keep their tag.

        Verified safe-to-ship via per-cluster what-if probe (/tmp/probe_iter4_p5_oneatatime.py):
          - closed_loop_replanning, exponential_backoff_retry,
            flash_attention_tiled_softmax, motion_blur_imu_aided_deblur,
            ppo_entropy_collapse_guard: 0/18 admit-set change
          - multi_stage_cc_cv_fast_charging: 1/18 (own task T_007 admit narrows)
          - sliding_window_kv_cache: 1/18 (T_008 admit, llm-inference replaces SE-tooling)
        Combined 7-tag bundle on top of iter4_p4: 3/18 changes
        (T_006 add llm-inference admit2, T_007 narrow to battery-only, T_W_008 flip
        llm-inference to top-1 — the actual target lift).

        Dropping any of these tags would re-open the silent-drop failure mode
        the iter4_p4 memory warns about.
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        iter4_p5_tagged = {
            "sliding_window_kv_cache",
            "closed_loop_replanning",
            "ppo_entropy_collapse_guard",
            "multi_stage_cc_cv_fast_charging",
            "motion_blur_imu_aided_deblur",
            "exponential_backoff_retry",
            "flash_attention_tiled_softmax",
        }
        by_id = {p.pattern_id: p for p in CURATED_SAFETY_PATTERNS}
        missing = []
        for cid in iter4_p5_tagged:
            p = by_id.get(cid)
            assert p is not None, f"{cid} missing from curated registry"
            if p.topic_tag is None:
                missing.append(cid)
        assert not missing, (
            f"iter4_p5 cluster(s) lost topic_tag: {missing}. "
            "These were tagged in iter4_p5 (commit after 1f09cf1) to lift T_W_008 "
            "admit-set into llm-inference-efficiency. Dropping the tag drops the "
            "cluster from its group's fingerprint."
        )


class TestLiveBridgeReflectsTopicGroup:
    """Post-republish smoke: the actual bridge file MUST carry topic_group.

    Skipped if no bridge is present locally (CI fresh-checkout case).
    """

    def test_anti_windup_pid_has_topic_group_in_bridge(self):
        bridge_path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "assets"
            / "bridge_index.json"
        )
        if not bridge_path.exists():
            pytest.skip("bridge_index.json not present locally")
        data = json.loads(bridge_path.read_text(encoding="utf-8"))
        c = data.get("symptom_clusters", {}).get("anti_windup_pid")
        if c is None:
            pytest.skip("anti_windup_pid absent from bridge — publisher not run yet")
        tg = c.get("topic_group")
        assert tg == "control-loop-stability", (
            f"anti_windup_pid.topic_group should be 'control-loop-stability' "
            f"after iter4_p3 publish; got {tg!r}. Re-run curated_publisher."
        )
