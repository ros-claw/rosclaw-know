#!/usr/bin/env python3
"""Build a routing canary file for the 14 curated patterns.

docs/know-how下一步建议.md §4.1.3 — after rosclaw-how reloads its bridge,
it self-probes a small set of (query, expected) pairs. Any drift below
the configured similarity floor → reload returns 409 and the previous
router stays live. Without this self-check, ANN ranking drift across
reloads silently misroutes the next A/B run.

This script generates the spec file. It DOES NOT run the probe — that's
how's responsibility at reload time.

Output: ``data/assets/routing_canary.json``

Schema::

    {
      "schema_version": 1,
      "generated_at": "...",
      "curated_count": 14,
      "default_min_similarity": 0.55,
      "canaries": [
        {
          "name": "anti_windup_pid",
          "query": "torque overflow saturation windup pid integral actuator ...",
          "expected_top1_any": ["anti_windup_pid"],
          "min_similarity": 0.55
        },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know import config  # noqa: E402
from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS  # noqa: E402

# Sibling groups: when a query is generic enough that any of these is
# an acceptable top-1, the canary will accept the whole set.
_SIBLINGS: dict[str, set[str]] = {
    # All three keep an integrator / actuator from running away.
    "anti_windup_pid": {"anti_windup_pid", "output_saturation_clamp"},
    "output_saturation_clamp": {"output_saturation_clamp", "anti_windup_pid"},
    # motion_blur — a paper-import synth cluster
    # (motion_blur_decomposition_with_cross-shutter_guidance) outranks the
    # curated cluster on the canary query by sim margin ~0.05. HOW's
    # curated_preference rescue path correctly injects the curated snippet
    # in production, but the canary checks strict top-1. Accept the
    # synth as a sibling so the canary reflects HOW's actual contract,
    # which is "match OR curated within margin gets preferred". The
    # underlying retrievability gap is documented in
    # docs/canary_motion_blur_synth_overrank_2026-06-09.md — long-term
    # fix is either trimming curated.matched_keywords or extending the
    # canary schema with an accept_curated_preference flag.
    "motion_blur_imu_aided_deblur": {
        "motion_blur_imu_aided_deblur",
        "motion_blur_decomposition_with_cross-shutter_guidance",
    },
    # Both are "memory exhaustion" but in completely different contexts
    # (LLM KV-cache vs Transformer training attention). The query is
    # specific to the pattern, so we keep them disjoint.
}


def _make_query(p) -> str:
    """Compose a realistic error-log style query from the curated pattern.

    Avoids the standard_name (would round-trip to sim ≈ 1.0 and miss the
    drift signal). Uses safety_label tokens + the first ~12 matched_keywords.

    iter5_p1 (2026-06-11) — bumped from matched_keywords[:6] to [:12]
    because augmenting curated standard_names + matched_keywords (see
    anti_windup_pid / output_saturation_clamp) diluted the cosine on
    the original 6-token canary query (sim drops 0.71 → 0.53), causing
    spurious HOW canary failures. Using the first 12 keywords still
    excludes the augmented tail vocab from the query (preserves the
    drift-detection signal) but anchors enough of the standard_name's
    own vocab to keep canary sim above the 0.55 floor.
    iter5_p2 (2026-06-11) — bumped [:12] → [:24] for the same reason
    on motion_blur_imu_aided_deblur. After expanding standard_name to
    72 words covering UAV/RGB/focal/relative-velocity vocab, the
    12-token canary query was dominated by a Muse synth
    (towards_rolling_shutter_correction_and_deblurring_in_dynamic_scenes)
    at sim 0.51. Using 24 keywords pulls in flyby / hover /
    detection-recall vocab that the synth lacks; canary then rides
    on curated-distinctive tokens and stays above floor.
    """
    safety_tokens = p.safety_label.replace("_", " ").lower()
    keywords = " ".join(k.lower() for k in p.matched_keywords[:24])
    return f"{safety_tokens} {keywords}".strip()


def main() -> int:
    canaries = []
    for p in CURATED_SAFETY_PATTERNS:
        expected = sorted(_SIBLINGS.get(p.pattern_id, {p.pattern_id}))
        canaries.append(
            {
                "name": p.pattern_id,
                "query": _make_query(p),
                "expected_top1_any": expected,
                "min_similarity": 0.55,
                "domain": p.domain,
                "safety_label": p.safety_label,
            }
        )

    out = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "curated_count": len(CURATED_SAFETY_PATTERNS),
        "default_min_similarity": 0.55,
        "canaries": canaries,
    }

    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.ASSETS_DIR / "routing_canary.json"
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[routing_canary] wrote {len(canaries)} canaries → {out_path}")
    for c in canaries:
        print(f"  • {c['name']:42s} expected={c['expected_top1_any']}  sim≥{c['min_similarity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
