"""Surgical overrides applied to non-curated clusters during publishing.

docs/know-how下一步建议.md §7 follow-on — sometimes a Muse synth (or
autodraft) cluster is genuinely competing with a curated and should be
suppressed even though the inference rules in :mod:`source_tier` would
otherwise put it at C_MUSE_SYNTH. The classic case (iter4_p1, 2026-06-10):

    cluster `pid_antiwindup` (synth, 985-char standard_name describing
    PID anti-windup back-calculation / conditional integration / output
    saturation clamp) outranks BOTH:
      - `anti_windup_pid`        (curated, S_CURATED_VERIFIED, 59 chars)
      - `output_saturation_clamp` (curated, S_CURATED_VERIFIED, 66 chars)

    on cosine retrieval for T_001 PIDTuning's symptom (sim ~0.79 synth
    vs ~0.72 curated). HOW's curated rescue doesn't fire because the
    curated isn't in top-K. The synth's snippet content is plausible
    but not as canonical-mapping-clean as the curated's.

This module lets us flip such synth clusters to F_DEMOTED so HOW's
existing demoted-skip path drops them from routing entirely. It's a
deliberate hack — the cleaner long-term fix is HOW's
ROSCLAW_HOW_TIER_AWARE_RANKING flag (S > A > B > C at same sim).

But the demotion is:
- **Surgical**: only the listed cluster_ids are demoted, no broad rule.
- **Auditable**: each entry MUST carry a reason string explaining the
  competition + the curated that displaces it.
- **Reversible**: delete the dict entry to undemote.
- **Cumulative with HOW's flag**: if both are on, the synth is dropped
  AND the tier-aware ranking still applies to remaining candidates.

How it composes with infer_source_tier:
  - If a cluster id is in SYNTH_DEMOTIONS, override returns F_DEMOTED
    regardless of metadata.
  - Curated clusters (source == "curated") are NEVER overridden — even
    if accidentally listed.
  - Idempotent: applying the override twice gives the same tier.
"""
from __future__ import annotations

from typing import Any, Final

from .source_tier import F_DEMOTED, infer_source_tier

# (cluster_id → reason). Each reason must explain:
#   (1) which curated cluster this synth competes with
#   (2) the evidence (paired_ab result, live probe, retrieval data)
#   (3) why demotion beats other mitigations
SYNTH_DEMOTIONS: Final[dict[str, str]] = {
    "pid_antiwindup": (
        "Competes with curated anti_windup_pid (S_CURATED_VERIFIED) and "
        "output_saturation_clamp (S_CURATED_VERIFIED) for T_001 PIDTuning "
        "routing. Live probe 2026-06-10: synth sim=0.7916 outranks both "
        "curated; HOW's curated rescue doesn't fire because the curated "
        "isn't in top-K. Synth's 985-char standard_name dominates cosine "
        "against any PID-windup symptom. The two curated cover the "
        "canonical fix family completely (back-calculation + output clamp), "
        "so suppressing this synth removes the competition without "
        "information loss. Probe_panel_rest shows T_001 has headroom 2.20 — "
        "this is one of only 3 panel tasks where curated lift is worth "
        "fighting for. (alt mitigation: HOW's "
        "ROSCLAW_HOW_TIER_AWARE_RANKING flag — keep both for defense-in-depth.)"
    ),
    "reflections_of_a_process_control_practitioner": (
        "iter5_p0 (2026-06-11). Wrong-domain synth from a process-control "
        "practitioner's reflective essay; standard_name is generic 'PID "
        "controller tuning is inconsistent or suboptimal due to process "
        "dead time, inverse response, or slow response'. matched_keywords "
        "are all 8 high-frequency generic terms (controller, tuning, "
        "process, control, locomotion, ...). Lives in topic_group "
        "control-loop-stability — the SAME group as the three curated "
        "anti_windup_pid / output_saturation_clamp / pid_joint_latency_"
        "oscillation — so HOW's topic-filter doesn't exclude it. "
        "routing_panel live-probe 2026-06-11 (verify_frontier_eng symptoms "
        "verbatim @ HOW :8088): "
        "- T_W_005 ActuatorOvershoot: this synth wins sim=0.6554, "
        "  output_saturation_clamp absent from top-1 "
        "- T_W_007 IntegrationWindup: this synth wins sim=0.6316, "
        "  anti_windup_pid absent from top-1 "
        "The synth's content is a generic essay, not a canonical mapping. "
        "The three curated cover the actual fix families (back-calculation, "
        "output clamp, joint-deadtime) more precisely. iter4_p9 fixed T_001 "
        "by adding pid_joint_latency_oscillation but did NOT address the "
        "synth's ongoing domination of T_W_005 / T_W_007 — that was a "
        "narrow surgical fix. This demotion is the structural fix for the "
        "broader PID-domain synth-vs-curated competition."
    ),
}


def apply_synth_overrides(cluster: dict[str, Any], cluster_id: str) -> str | None:
    """Return an override tier for this cluster, or None if no override applies.

    SIDE EFFECTS when returning F_DEMOTED:

    1. Mutate ``cluster["priority"] = -1``. This is the actual signal
       rosclaw-how's asset_loader checks (`priority < 0` → drop the row
       and increment demoted_skipped). Without this, source_tier=F_DEMOTED
       is a label without consequence — HOW happily continues serving
       the cluster from seekdb.

    2. Mutate ``cluster["metadata"]["lifecycle_status"] = "demoted"``
       (purely for observability — HOW's filter doesn't read it, but
       any downstream tooling that does will see the demotion).

    Both mutations are idempotent: re-applying produces the same result.
    Existing demotion reasons (e.g. set by a prior Muse pass) are
    preserved.

    Curated clusters are NEVER overridden — if a curated cluster_id ends up
    in SYNTH_DEMOTIONS by mistake, we return None and let infer_source_tier
    keep the S_CURATED_VERIFIED. (Test test_curated_never_overridden locks
    this in.)
    """
    if cluster.get("source") == "curated":
        return None
    if cluster_id in SYNTH_DEMOTIONS:
        # The signal HOW actually filters on. priority < 0 → demoted_skipped.
        cluster["priority"] = -1
        # Observability — preserve any existing demotion reason.
        md = cluster.setdefault("metadata", {})
        if md.get("lifecycle_status") != "demoted":
            md["lifecycle_status"] = "demoted"
            md["lifecycle_status_reason"] = "synth_overrides_demotion"
        return F_DEMOTED
    return None


def infer_source_tier_with_overrides(cluster: dict[str, Any], cluster_id: str) -> str:
    """Like infer_source_tier(cluster), but consults SYNTH_DEMOTIONS first.

    The publisher should call THIS function (not infer_source_tier directly)
    so that overrides take effect.
    """
    override = apply_synth_overrides(cluster, cluster_id)
    if override is not None:
        return override
    return infer_source_tier(cluster)


__all__ = [
    "SYNTH_DEMOTIONS",
    "apply_synth_overrides",
    "infer_source_tier_with_overrides",
]
