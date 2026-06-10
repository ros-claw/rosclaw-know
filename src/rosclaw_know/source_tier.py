"""Source-tier ladder per docs/know-how下一步建议.md §5.3.

The runtime router can use the ladder to weight curated above synth above
trajectory-mined above autodraft. Tiers are stable string enums; the
publisher stamps them on every cluster so ``content_hash`` covers them
(see ``ROUTING_CRITICAL_FIELDS`` in :mod:`curated_publisher`).

Ladder (highest confidence first)::

    S_CURATED_VERIFIED   Hand-authored + paired-AB validated (n=30 vs control)
    A_CURATED_REVIEWED   Hand-authored draft, not yet validated end-to-end
    B_TRAJECTORY_MINED   Distilled from real trajectories, evidence.n >= 2
    C_MUSE_SYNTH         LLM-synthesized from research corpus, unvalidated
    D_AUTODRAFT          Phase-7 autodrafted, surfaces only after topic_group
                         inference completes
    F_DEMOTED            Explicitly demoted via lifecycle_status or admin op

The inference rule below is a pure function of ``cluster.metadata`` — the
publisher calls it once per cluster on every publish; the only fast path
is "already has a tier → leave it alone" for already-curated entries.
"""
from __future__ import annotations

from typing import Any

# Stable enum strings — also serialized into bridge_index.json so any rename
# is a breaking schema change.
S_CURATED_VERIFIED = "S_CURATED_VERIFIED"
A_CURATED_REVIEWED = "A_CURATED_REVIEWED"
B_TRAJECTORY_MINED = "B_TRAJECTORY_MINED"
C_MUSE_SYNTH = "C_MUSE_SYNTH"
D_AUTODRAFT = "D_AUTODRAFT"
F_DEMOTED = "F_DEMOTED"

SOURCE_TIER_LADDER: tuple[str, ...] = (
    S_CURATED_VERIFIED,
    A_CURATED_REVIEWED,
    B_TRAJECTORY_MINED,
    C_MUSE_SYNTH,
    D_AUTODRAFT,
    F_DEMOTED,
)


# Evidence thresholds — promoting C_MUSE_SYNTH → B_TRAJECTORY_MINED requires
# both a non-trivial sample size AND a positive observed uplift. n=2 is a
# bare floor: by then we've seen the cluster fire on at least two distinct
# trajectories, not just one. A separate avg_uplift > 0 gate prevents
# promoting clusters that fired but didn't actually help.
B_TIER_MIN_EVIDENCE_N = 2
B_TIER_MIN_AVG_UPLIFT = 0.0  # strictly > this


def infer_source_tier(cluster: dict[str, Any]) -> str:
    """Return the appropriate ``source_tier`` for this cluster.

    Order of precedence (first match wins):

    1. Explicit ``source_tier`` already set → leave it.
    2. ``source == 'curated'`` → S_CURATED_VERIFIED.
    3. ``metadata.lifecycle_status == 'demoted'`` (or ``source_tier`` was
       previously set to F_DEMOTED and stripped by a bad migration) →
       F_DEMOTED.
    4. ``metadata.autodrafted == True`` or ``metadata.source == 'autodraft'``
       → D_AUTODRAFT.
    5. Evidence-promoted: ``metadata.evidence.n >= 2`` AND
       ``metadata.evidence.avg_uplift > 0`` → B_TRAJECTORY_MINED.
    6. Otherwise → C_MUSE_SYNTH.
    """
    explicit = cluster.get("source_tier")
    if explicit in SOURCE_TIER_LADDER:
        return explicit

    if cluster.get("source") == "curated":
        return S_CURATED_VERIFIED

    md = cluster.get("metadata") or {}

    lifecycle = md.get("lifecycle_status")
    if lifecycle == "demoted":
        return F_DEMOTED

    if md.get("autodrafted") or md.get("auto_drafted") or md.get("source") == "autodraft":
        return D_AUTODRAFT

    evidence = md.get("evidence") or {}
    try:
        n = int(evidence.get("n", 0) or 0)
        uplift = float(evidence.get("avg_uplift", 0.0) or 0.0)
    except (TypeError, ValueError):
        n, uplift = 0, 0.0
    if n >= B_TIER_MIN_EVIDENCE_N and uplift > B_TIER_MIN_AVG_UPLIFT:
        return B_TRAJECTORY_MINED

    return C_MUSE_SYNTH


__all__ = [
    "infer_source_tier",
    "SOURCE_TIER_LADDER",
    "S_CURATED_VERIFIED",
    "A_CURATED_REVIEWED",
    "B_TRAJECTORY_MINED",
    "C_MUSE_SYNTH",
    "D_AUTODRAFT",
    "F_DEMOTED",
    "B_TIER_MIN_EVIDENCE_N",
    "B_TIER_MIN_AVG_UPLIFT",
]
