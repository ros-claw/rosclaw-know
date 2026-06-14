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
import json
import logging
import re
from pathlib import Path
from typing import Any

from . import config
from .bridge_schema import (
    ROUTING_CRITICAL_FIELDS,
    compute_content_hash,
    compute_metadata_hash,
)
from .curated_conflict_detector import detect_conflicts, format_report
from .curated_patterns import CuratedPattern
from .curated_registry import load_curated_patterns
from .source_tier import S_CURATED_VERIFIED
from .synth_overrides import infer_source_tier_with_overrides

log = logging.getLogger("rosclaw_know.curated_publisher")


# Re-exported from bridge_schema so tests and scripts can reference the same
# routing-critical field list without duplicating it.


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

    # Bridge Schema v2: source_tier, status, runtime_eligible, priority,
    # robot_type, routing_guard, evidence, and demotion come from the curated
    # registry (or legacy defaults for the old constant list).
    source_tier = p.source_tier or S_CURATED_VERIFIED
    status = p.status or "active"
    runtime_eligible = bool(p.runtime_eligible)
    if status == "demoted" or not runtime_eligible:
        priority = -1
    else:
        priority = 1

    entry: dict[str, Any] = {
        "standard_name": p.standard_name,
        "domain": p.domain,
        "robot_type": p.robot_type,
        "safety_label": p.safety_label,  # used by rosclaw-how for exact match
        "source": "curated",  # so the runtime can prefer these
        "source_tier": source_tier,
        "status": status,
        "runtime_eligible": runtime_eligible,
        "priority": priority,
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
    # iter4_p3 (2026-06-10) — topic_group routes the curated into HOW's
    # topic-filtered candidate pool. Without this, curated is invisible to
    # ``topic_filter_path=top1`` even when its cosine sim would rank it in
    # top-K. Only emit when set so the field stays absent for any future
    # curated that legitimately doesn't fit an existing topic group.
    if p.topic_group is not None:
        entry["topic_group"] = p.topic_group
    # iter4_p4 (2026-06-10) — topic_tag is REQUIRED by HOW's
    # ``topic_group._build_group_to_fingerprint_text`` for this cluster's
    # standard_name to contribute to its group's fingerprint. A cluster
    # with topic_group but NO topic_tag is silently dropped from the
    # fingerprint compute, so it never influences which group its own
    # query routes to. iter4_p3 was structurally incomplete without this.
    if p.topic_tag is not None:
        entry["topic_tag"] = p.topic_tag

    # v2 governance fields — emitted only when present so the bridge stays
    # backward-compatible with older consumers that ignore them.
    if p.routing_guard:
        entry["routing_guard"] = p.routing_guard
    if p.evidence:
        entry["evidence"] = p.evidence
    if p.demotion:
        entry["demotion"] = p.demotion

    # Compute deterministic content_hash AFTER all routing-critical fields
    # are populated; the hash itself is excluded from the payload.
    entry["content_hash"] = compute_content_hash(entry)
    entry["metadata_hash"] = compute_metadata_hash(entry)
    return entry


_SAFETY_LABEL_RE = re.compile(r"^[A-Z][A-Za-z_]+$")


def publish_curated_assets() -> dict[str, int]:
    """Graft curated entries into the existing bridge_index.json + code_patterns/.

    Idempotent. Overwrites any existing entries whose key matches a curated
    ``pattern_id``. Returns counts for the run log.
    """
    patterns = load_curated_patterns()
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    config.CODE_PATTERNS_DIR.mkdir(parents=True, exist_ok=True)

    # P1 §7 — surface curated registry collisions BEFORE grafting so
    # silent K-CURATED-006-style bugs are caught here, not in a paired-AB
    # surprise. Doesn't block; just logs. The unit test
    # test_known_safety_label_collisions_documented is the gate that
    # FAILS when a new (un-acknowledged) collision appears.
    conflicts = detect_conflicts(patterns)
    if conflicts:
        log.warning("Curated publisher: %s", format_report(conflicts))

    bridge_path = config.ASSETS_DIR / "bridge_index.json"
    if bridge_path.exists():
        with open(bridge_path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {"schema_version": 2, "symptom_clusters": {}}
    data.setdefault("symptom_clusters", {})
    # Bridge Schema v2 — numeric version for strong typing.
    data["schema_version"] = 2

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
    for p in patterns:
        data["symptom_clusters"][p.pattern_id] = _build_cluster_entry(p)
        written_pattern_files.append(str(_write_pattern_md(p)))
        safety_lookup.setdefault(p.safety_label, []).append(p.pattern_id)

    # P0 §4.1.1 / P1 §5.3 / iter4_p2 backfill in lockstep — stamping
    # source_tier (or applying a synth_overrides demotion) MUST recompute
    # content_hash because source_tier is in ROUTING_CRITICAL_FIELDS. The
    # publisher uses infer_source_tier_with_overrides so any cluster_id
    # listed in synth_overrides.SYNTH_DEMOTIONS gets F_DEMOTED, regardless
    # of what its metadata.evidence would otherwise imply.
    backfilled = 0
    tier_stamped = 0
    for cid, c in data["symptom_clusters"].items():
        if not isinstance(c, dict):
            continue
        tier_added = False
        new_tier = infer_source_tier_with_overrides(c, cid)
        if c.get("source_tier") != new_tier:
            c["source_tier"] = new_tier
            tier_added = True
            tier_stamped += 1
        if tier_added or "content_hash" not in c:
            # Tier change OR hash missing → (re)compute. Existing hashes
            # over clusters whose tier was already correct are left untouched.
            c["content_hash"] = compute_content_hash(c)
            c["metadata_hash"] = compute_metadata_hash(c)
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
        "curated_clusters": len(patterns),
        "curated_patterns": len(written_pattern_files),
        "safety_label_entries": len(safety_lookup),
        "content_hash_backfilled": backfilled,
        "source_tier_stamped": tier_stamped,
    }


__all__ = [
    "publish_curated_assets",
    "ROUTING_CRITICAL_FIELDS",
]
