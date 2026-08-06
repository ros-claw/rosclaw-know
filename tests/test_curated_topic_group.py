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
        # iter5_p2 (2026-06-11) — single-cluster group for
        # motion_blur_imu_aided_deblur. Was in 3d-perception-and-mapping
        # but that group's 53-cluster fingerprint is dominated by 52
        # SLAM / 3D-mapping / scene-reconstruction synths whose vocab
        # has little overlap with platform-motion-blur queries. Live
        # probe (2026-06-11) on T_010 UAV inspection: query landed on
        # rl-training-stability (0.38) + control-loop-stability (0.34)
        # at top-2, with 3d-perception-and-mapping not in top-5.
        # motion_blur_imu_aided_deblur was filtered out of CATALYST's
        # candidate pool entirely (sim 0.7180 against the cluster
        # itself, but topic_group admission rejected it). New dedicated
        # group lifts T_010's cluster admission. See
        # project_iter5_p2_motion_blur_topic_group_move.md.
        "motion-blur-deblur",
        # iter5_p3 (2026-06-11) — single-cluster group for
        # exponential_backoff_retry. Was in fault-tolerant-compute, but that
        # group's 7-cluster fingerprint is dominated by 6 checkpointing /
        # HPC fault-tolerance synths whose vocab has little overlap with
        # microservice / API / retry-storm queries. Live probe on T_W_003:
        # query admitted to [scheduling-optimization, software-engineering-and-tooling]
        # while exponential_backoff_retry (sim 0.7240) was filtered out of
        # CATALYST's candidate pool entirely. New dedicated group lifts
        # T_W_003's cluster admission. General name hosts future retry /
        # circuit-breaker / backpressure curated clusters. See
        # project_iter5_p3_exponential_backoff_topic_group_move.md.
        "reliability-engineering",
        # iter5_p4 (2026-06-11) — single-cluster group for
        # closed_loop_replanning. Was in llm-planning-and-reasoning, but that
        # group's fingerprint is dominated by LLM chain-of-thought / planner
        # synths whose vocab has little overlap with MPC / friction / horizon-
        # drift queries. Live probe on T_W_006: query admitted to
        # [Learning_Training, Planning_Decision, Control_Locomotion] while
        # closed_loop_replanning (in llm-planning-and-reasoning) was filtered
        # out of CATALYST's candidate pool entirely. New dedicated group lifts
        # T_W_006's cluster admission. General name hosts future MPC /
        # receding-horizon / closed-loop planning curated clusters.
        "closed-loop-replanning",
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

    def test_iter4_p6_anti_windup_pid_has_topic_tag(self):
        """iter4_p6 invariant: anti_windup_pid MUST carry topic_tag.

        Selected as the iter4_p6 standalone holdout ship per per-cluster what-if
        probe on iter4_p5 baseline (/tmp/probe_iter4_p6_holdouts_onp5.py):
          - 2 admit-set changes vs iter4_p5: T_001 (broadens to admit
            robot-morphology-and-constraints alongside control-loop-stability),
            T_W_006 (broadens to admit rl-training-stability alongside
            control-loop-stability).
          - T_001 is the ONLY AUTHOR_CURATED task (h=2.20 per 2026-06-09 probe)
            without current paired_ab lift (Δ̄=-0.9 in iter4_p5, within CI of 0
            but consistently negative across iter4_p4/p5). Targeting it.
          - T_W_006 has C̄=10.00 (LLM-saturated bimodal-judge) so admit-set
            broadening has zero measurable effect.

        Without this tag, anti_windup_pid contributes ZERO to control-loop-stability's
        fingerprint (HOW's _build_group_to_fingerprint_text requires both
        topic_group AND topic_tag). The fingerprint is dominated by 21 synth
        clusters' standard_names. Adding the tag activates anti_windup's
        ~5% contribution — pure structural insurance, neutral expected paired_ab
        outcome (similar framing to iter4_p5: ship for fingerprint coverage,
        not for measurable lift).
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        awp = next(
            (p for p in CURATED_SAFETY_PATTERNS if p.pattern_id == "anti_windup_pid"),
            None,
        )
        assert awp is not None, "anti_windup_pid missing from curated registry"
        assert awp.topic_tag is not None, (
            "anti_windup_pid needs topic_tag for its control-loop-stability "
            "group fingerprint to include it. Dropping the tag re-opens the "
            "silent-fingerprint-drop failure mode the iter4_p4 memory warns about."
        )
        assert awp.topic_group == "control-loop-stability"

    def test_iter4_p7_gradient_clipping_has_topic_tag(self):
        """iter4_p7 invariant: gradient_clipping MUST carry topic_tag.

        Chosen per cross-reference of:
          (a) 2026-06-09 headroom probe: T_W_002 GradExplosionRL has C̄=9.20
              (h=0.80, just below AUTHOR_CURATED threshold but still measurable
              gap, and is the only non-saturated wild cold spot per
              project_wild_cold_spots_llm_ceiling).
          (b) Autodraft `infer_autodraft_topic_group.py --dry-run --force`
              against iter4_p6 bridge: gradient_clipping would resolve to
              topic_group=rl-training-stability(sim=0.347) but topic_tag=None
              (kNN-1 lookup returns no labeled neighbor with a tag). So KNOW
              MUST author the tag explicitly — autodraft can't supply it.
          (c) iter4_p5 per-cluster what-if probe on iter4_p4 baseline:
              gradient_clipping has 3 admit-set changes (T_010, T_W_004, T_W_006)
              — all 3 are SKIP_CURATED per the 2026-06-09 probe so the admit-set
              broadening is structural-insurance only, no measurable paired_ab lift.

        T_W_002's current routing is via SAFETY path (Numerical_Instability label
        match), not CATALYST cluster cosine. So adding the topic_tag does NOT
        change T_W_002's inject. iter4_p7 is the same "no-regression structural
        insurance" framing as iter4_p5 and iter4_p6 — activates
        gradient_clipping's contribution to rl-training-stability group
        fingerprint without changing any task's routing.

        Long-term value: rl-training-stability currently has zero curated
        contribution to its fingerprint (anti_windup_pid is in control-loop-
        stability, ppo_entropy_collapse_guard's iter4_p5 tag isn't in
        rl-training-stability). gradient_clipping is the natural curated to
        anchor rl-training-stability's fingerprint.
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        gc = next(
            (p for p in CURATED_SAFETY_PATTERNS if p.pattern_id == "gradient_clipping"),
            None,
        )
        assert gc is not None, "gradient_clipping missing from curated registry"
        assert gc.topic_tag is not None, (
            "gradient_clipping needs topic_tag for its rl-training-stability "
            "group fingerprint to include it. Autodraft kNN-1 inference can't "
            "supply this tag (no labeled neighbor with a topic_tag matches), "
            "so KNOW publisher is the only canonical source."
        )
        assert gc.topic_group == "rl-training-stability"

    def test_iter4_p8_final_holdouts_have_topic_tag(self):
        """iter4_p8 invariant: the final 4 holdout curated MUST keep their tags.

        Batch ship completing 14/14 curated tagged. Per-cluster + batch what-if
        probe on iter4_p7 baseline (/tmp/probe_iter4_p8_holdouts_onp7.py):

          output_saturation_clamp:               2 admit changes (T_003, T_010)
          terrain_aware_locomotion:              2 admit changes (T_002, T_003)
          time_optimal_path_blending:            2 admit changes (T_002, T_003)
          metaheuristic_combinatorial_escape:    3 admit changes (T_004, T_008, T_W_003)
          BATCH (all 4):                         6 unique tasks affected
                                                  (T_002, T_003, T_004, T_008, T_010, T_W_003)

        ALL 6 affected tasks are SKIP_CURATED per 2026-06-09 headroom probe
        (T_002 is AUTHOR_CURATED h=2.60 but already saturated 10/10 via existing
        terrain_aware_locomotion routing). No measurable paired_ab lift expected.

        This completes the iter4 routing-tag arc. All 14 curated now declare
        topic_tag in their canonical KNOW representation, regardless of whether
        HOW also infers via autodraft (the 4 holdouts here all show topic_tag=None
        in autodraft --dry-run --force output, confirming KNOW publisher is the
        only canonical source).

        Dropping any of these tags re-opens the silent-fingerprint-drop failure
        mode the iter4_p4 memory documented.
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        iter4_p8_tagged = {
            "output_saturation_clamp",
            "terrain_aware_locomotion",
            "time_optimal_path_blending",
            "metaheuristic_combinatorial_escape",
        }
        by_id = {p.pattern_id: p for p in CURATED_SAFETY_PATTERNS}
        missing = []
        for cid in iter4_p8_tagged:
            p = by_id.get(cid)
            assert p is not None, f"{cid} missing from curated registry"
            if p.topic_tag is None:
                missing.append(cid)
        assert not missing, (
            f"iter4_p8 cluster(s) lost topic_tag: {missing}. "
            "These were tagged in iter4_p8 (commit after iter4_p7 43787ce) to "
            "complete the 14/14 curated tag coverage. Dropping the tag drops "
            "the cluster from its group's fingerprint."
        )

    def test_all_curated_have_topic_tag(self):
        """Post-iter4_p8 invariant: ALL 14 curated declare topic_tag.

        The iter4_p3 invariant required topic_group. iter4_p8 closes the loop
        by requiring topic_tag on every curated. With both invariants, the
        publisher emits complete fingerprint data for every curated cluster,
        and HOW's _build_group_to_fingerprint_text picks them all up.
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        missing_tag = [
            p.pattern_id for p in CURATED_SAFETY_PATTERNS if p.topic_tag is None
        ]
        assert not missing_tag, (
            f"{len(missing_tag)} curated pattern(s) without topic_tag: "
            f"{missing_tag}. iter4_p8 completed the 14/14 tag coverage; any "
            "future curated additions must declare topic_tag too."
        )

    def test_iter4_p9_pid_joint_latency_oscillation_present(self):
        """iter4_p9 invariant: pid_joint_latency_oscillation MUST be present.

        Authored as a NEW curated (not augment) to win T_001 PIDTuning's
        cluster cosine over the synth `reflections_of_a_process_control_practitioner`
        (sim 0.6419 — wrong domain, process-dead-time vs T_001's anti-windup+latency).

        standard_name engineered to:
          (a) WIN T_001: vocab "robotic-arm joint" + "sensor-to-actuator loop
              latency" + "sustained oscillation" + "integral term accumulating"
              matches T_001 symptom directly.
          (b) NOT WIN T_W_005: T_W_005's "voice-coil actuator" + "force overshoot"
              + "rated peak" vocab is absent from this standard_name.
          (c) NOT WIN T_W_007: T_W_007's "flow-rate" + "control valve" + "demand
              transient" vocab is absent from this standard_name.

        Per iter5 lesson [[project-iter5-anti-windup-pid-augment-reverted]],
        augmenting anti_windup_pid's standard_name is risky (preference swaps
        on T_W_005/T_W_007 with home-turf noise dominating paired_ab).
        A NEW cluster bypasses this risk: T_001 gets its own cluster cosine
        winner without anti_windup_pid changing at all, so T_W_005/T_W_007
        routing stays anchored.

        Verified safe-to-ship via offline what-if probe before live publish
        (see iter4_p9 memory for forensic).
        """
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
        p = next(
            (p for p in CURATED_SAFETY_PATTERNS if p.pattern_id == "pid_joint_latency_oscillation"),
            None,
        )
        assert p is not None, (
            "pid_joint_latency_oscillation missing from curated registry. "
            "This is the iter4_p9 ship targeting T_001 — removing it reverts "
            "T_001 routing back to the wrong synth."
        )
        assert p.topic_group == "control-loop-stability", (
            f"pid_joint_latency_oscillation must be in control-loop-stability, "
            f"got {p.topic_group!r}"
        )
        assert p.topic_tag is not None
        # Standard_name MUST include T_001-distinctive vocab; if these tokens
        # are removed the cluster won't win T_001 cluster cosine.
        sn = p.standard_name.lower()
        for token in ["robotic-arm", "joint", "deadtime", "oscillation", "30 ms"]:
            assert token in sn, (
                f"pid_joint_latency_oscillation standard_name missing required "
                f"T_001-distinctive token: {token!r}. Full: {p.standard_name!r}"
            )
        # Anti-collision: standard_name should NOT include T_W_005's "voice-coil"
        # or T_W_007's "flow-rate" / "control valve". If those appear, the new
        # cluster may steal routing from anti_windup_pid on those tasks.
        for forbidden in ["voice-coil", "voice coil", "flow-rate", "flow rate", "control valve"]:
            assert forbidden not in sn, (
                f"pid_joint_latency_oscillation standard_name contains "
                f"T_W_005/T_W_007 collision token: {forbidden!r}. Removing "
                f"these prevents preference swap on anti_windup_pid's current "
                f"home tasks."
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
