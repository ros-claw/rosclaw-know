"""Curated-pattern publisher.

Runs at the END of the Phase-1 pipeline to graft hand-written safety
patterns into ``bridge_index.json`` and ``code_patterns/``. These patterns
are guaranteed to be present even when Muse hasn't yet found organic
neighbours for a given safety label — rosclaw-how's runtime depends on the
named patterns (``anti_windup_pid``, ``sliding_window_kv_cache``, …)
existing for exact-match safety routing.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from . import config
from .curated_patterns import CURATED_SAFETY_PATTERNS, CuratedPattern
from .source_tier import infer_source_tier

log = logging.getLogger("rosclaw_know.curated_publisher")


# P0 Week-1 from docs/know-how下一步建议.md §4.1.1 — routing-critical fields
# must trigger a content_hash change so that rosclaw-how's `/admin/reload`
# can detect "this cluster needs re-embed" vs "metadata-only update".
# Order doesn't matter; presence does.
ROUTING_CRITICAL_FIELDS: tuple[str, ...] = (
    "standard_name",
    "domain",
    "topic_group",
    "topic_tag",
    "matched_keywords",
    "cross_domain_analogies",
    "associated_patterns",
    "source",
    "source_tier",
    "safety_label",
    "priority",
    "snippet_mode_hint",
)


def _normalize_for_hash(value: Any) -> Any:
    """Canonicalize a value before hashing so order-insensitive lists hash stably."""
    if isinstance(value, list):
        # Sort list of primitives by string repr; list of dicts by JSON repr.
        try:
            return sorted(_normalize_for_hash(v) for v in value)
        except TypeError:
            return sorted(
                (_normalize_for_hash(v) for v in value),
                key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False),
            )
    if isinstance(value, dict):
        return {k: _normalize_for_hash(value[k]) for k in sorted(value)}
    return value


def compute_cluster_content_hash(cluster: dict[str, Any]) -> str:
    """Deterministic sha256 over the routing-critical subset of a cluster.

    The hash deliberately excludes ``content_hash`` itself and any
    ephemeral observability fields (uplift counters, last-touched
    timestamps, evidence rollups). Two clusters that produce the same
    hash should be byte-equivalent from the runtime router's perspective.
    """
    payload = {
        f: _normalize_for_hash(cluster.get(f))
        for f in ROUTING_CRITICAL_FIELDS
        if f in cluster
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _build_unified_diff(p: CuratedPattern) -> str:
    diff = difflib.unified_diff(
        p.before_code.splitlines(keepends=True),
        p.after_code.splitlines(keepends=True),
        fromfile=f"{p.pattern_id}.before.py",
        tofile=f"{p.pattern_id}.after.py",
        lineterm="",
    )
    return "".join(diff)


def _write_pattern_md(p: CuratedPattern) -> Path:
    out_path = config.CODE_PATTERNS_DIR / f"{p.pattern_id}.md"
    diff = _build_unified_diff(p)

    body = [
        "---",
        f"pattern_id: {p.pattern_id}",
        f"safety_label: {p.safety_label}",
        f"applicable_symptoms: [{p.pattern_id}]",
        f"domain: {p.domain}",
        "source: curated",
        "---",
        "",
        f"# {p.standard_name}",
        "",
        f"**Domain**: `{p.domain}`",
        f"**Safety label**: `{p.safety_label}`",
        "",
        "## Fix",
        "",
        p.fix_pattern,
        "",
        "## Anti-pattern",
        "",
        p.failed_attempt,
        "",
    ]
    if p.cross_domain_hints:
        body.append("## Cross-domain analogies (curated)")
        body.append("")
        for h in p.cross_domain_hints:
            body.append(f"- **{h['source_domain']}** → {h['insight']}")
            body.append(f"  - related fix: {h['action_suggestion']}")
        body.append("")
    body.append("## Patch")
    body.append("")
    body.append("```diff")
    body.append(diff)
    body.append("```")
    out_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return out_path


def _build_cluster_entry(p: CuratedPattern) -> dict[str, Any]:
    # The runtime's normalize_error spits out safety labels in CamelCase
    # (Memory_Exhaustion etc.). We expose both the safety label and a small
    # keyword set so that even a CamelCase exact-string-match works alongside
    # the vector search.
    keyword_set = sorted(
        {
            p.safety_label.lower(),
            p.safety_label.replace("_", " ").lower(),
            *[k.lower() for k in p.matched_keywords],
        }
    )
    entry: dict[str, Any] = {
        "standard_name": p.standard_name,
        "domain": p.domain,
        "safety_label": p.safety_label,            # used by rosclaw-how for exact match
        "source": "curated",                       # so the runtime can prefer these
        # P0 Bridge-Schema v2 docs §5.3 — all 14 hand-curated patterns have
        # passed n=30 paired A/B vs no-injection (see docs/REPORT_2026-06-09.md);
        # they ship as S_CURATED_VERIFIED. Future drafts not yet validated
        # would land as A_CURATED_REVIEWED.
        "source_tier": "S_CURATED_VERIFIED",
        "matched_keywords": keyword_set,
        "cross_domain_analogies": [
            {
                "source_domain": h["source_domain"],
                "insight": h["insight"],
                "action_suggestion": h["action_suggestion"],
                "neighbor_id": "curated",
            }
            for h in p.cross_domain_hints
        ],
        "associated_patterns": [p.pattern_id],
    }
    # Compute deterministic content_hash AFTER all routing-critical fields
    # are populated; the hash itself is excluded from the payload.
    entry["content_hash"] = compute_cluster_content_hash(entry)
    return entry


_SAFETY_LABEL_RE = re.compile(r"^[A-Z][A-Za-z_]+$")


def publish_curated_assets() -> dict[str, int]:
    """Graft curated entries into the existing bridge_index.json + code_patterns/.

    Idempotent. Overwrites any existing entries whose key matches a curated
    ``pattern_id``. Returns counts for the run log.
    """
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    config.CODE_PATTERNS_DIR.mkdir(parents=True, exist_ok=True)

    bridge_path = config.ASSETS_DIR / "bridge_index.json"
    if bridge_path.exists():
        with open(bridge_path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {"schema_version": "v2", "symptom_clusters": {}}
    data.setdefault("symptom_clusters", {})
    data.setdefault("schema_version", "v2")

    # Optional top-level reverse-lookup table — populated regardless of which
    # Muse run produced the bridge; rosclaw-how can consult it for an O(1)
    # safety-label → pattern_id shortcut.
    #
    # Schema: dict[str, list[str]] — a label can map to multiple curated
    # patterns when more than one applies (e.g. Memory_Exhaustion is
    # claimed by both sliding_window_kv_cache and flash_attention_tiled_softmax).
    # The previous dict[str, str] form silently lost everything but the
    # last-iterated pattern, which broke K-CURATED-006.
    safety_lookup: dict[str, list[str]] = {}

    written_pattern_files: list[str] = []
    for p in CURATED_SAFETY_PATTERNS:
        data["symptom_clusters"][p.pattern_id] = _build_cluster_entry(p)
        written_pattern_files.append(str(_write_pattern_md(p)))
        safety_lookup.setdefault(p.safety_label, []).append(p.pattern_id)

    # P0 §4.1.1 / P1 §5.3 backfill in lockstep — stamping source_tier
    # MUST recompute content_hash (tier is a ROUTING_CRITICAL_FIELD). The
    # two backfills share a single loop so we don't run into "tier stamped
    # but hash still excludes it" state. If neither needs touching, leave
    # the cluster alone (idempotent reload).
    backfilled = 0
    tier_stamped = 0
    for cid, c in data["symptom_clusters"].items():
        if not isinstance(c, dict):
            continue
        tier_added = False
        if "source_tier" not in c:
            c["source_tier"] = infer_source_tier(c)
            tier_added = True
            tier_stamped += 1
        if tier_added or "content_hash" not in c:
            # Tier change OR hash missing → (re)compute. Existing hashes
            # over clusters whose tier was already set are left untouched
            # (the on-disk hash is canonical for that cluster).
            c["content_hash"] = compute_cluster_content_hash(c)
            backfilled += 1

    data["safety_label_index"] = safety_lookup

    bridge_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "Curated publisher: %d patterns grafted, safety_label_index has %d entries, "
        "content_hash backfilled on %d clusters, source_tier stamped on %d clusters",
        len(written_pattern_files),
        len(safety_lookup),
        backfilled,
        tier_stamped,
    )
    return {
        "curated_clusters": len(CURATED_SAFETY_PATTERNS),
        "curated_patterns": len(written_pattern_files),
        "safety_label_entries": len(safety_lookup),
        "content_hash_backfilled": backfilled,
        "source_tier_stamped": tier_stamped,
    }


__all__ = [
    "publish_curated_assets",
    "compute_cluster_content_hash",
    "ROUTING_CRITICAL_FIELDS",
]
