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
from .curated_patterns import CURATED_SAFETY_PATTERNS, CuratedPattern

log = logging.getLogger("rosclaw_know.curated_publisher")


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
    return {
        "standard_name": p.standard_name,
        "domain": p.domain,
        "safety_label": p.safety_label,            # used by rosclaw-how for exact match
        "source": "curated",                       # so the runtime can prefer these
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
        data = {"symptom_clusters": {}}
    data.setdefault("symptom_clusters", {})

    # Optional top-level reverse-lookup table — populated regardless of which
    # Muse run produced the bridge; rosclaw-how can consult it for an O(1)
    # safety-label → pattern_id shortcut.
    safety_lookup: dict[str, str] = {}

    written_pattern_files: list[str] = []
    for p in CURATED_SAFETY_PATTERNS:
        data["symptom_clusters"][p.pattern_id] = _build_cluster_entry(p)
        written_pattern_files.append(str(_write_pattern_md(p)))
        safety_lookup[p.safety_label] = p.pattern_id

    data["safety_label_index"] = safety_lookup

    bridge_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "Curated publisher: %d patterns grafted, safety_label_index has %d entries",
        len(written_pattern_files),
        len(safety_lookup),
    )
    return {
        "curated_clusters": len(CURATED_SAFETY_PATTERNS),
        "curated_patterns": len(written_pattern_files),
        "safety_label_entries": len(safety_lookup),
    }


__all__ = ["publish_curated_assets"]
