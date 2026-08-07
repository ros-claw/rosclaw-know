"""Explainable source-authority classification.

Derived services accelerate discovery, but never outrank their pinned primary
source.  The tier is intentionally categorical rather than folded into a
single opaque confidence value.
"""

from __future__ import annotations

from rosclaw_know.contracts import SourceRecordV2


def source_authority(source: SourceRecordV2) -> tuple[str, float, list[str]]:
    if source.provenance_status == "untrusted":
        return "D", 0.2, ["source is explicitly untrusted"]
    if source.source_type.startswith("derived_") or source.provenance_status == "generated":
        return "B", 0.65, ["derived source; direct-source verification required"]
    if source.trust_tier == "official":
        return "S", 1.0, ["official primary publication"]
    if source.trust_tier == "primary":
        if source.source_type in {"issue", "pull_request"}:
            return "A", 0.85, ["maintainer-level repository record"]
        return "S", 0.95, ["pinned primary source"]
    if source.trust_tier == "curated":
        return "B", 0.7, ["curated derived source"]
    if source.trust_tier == "community":
        return "C", 0.45, ["community source"]
    return "D", 0.25, ["source authority is unknown"]


__all__ = ["source_authority"]
