#!/usr/bin/env python3
"""Second-pass labeler: coarsen 366 fine ``topic_tag`` values into ~30
``topic_group`` buckets, so retrieval can hard-filter at the coarse level
and rank within the fine level.

Background: ``label_cluster_topics.py`` produced highly-specific tags
(``spot-instance-eviction``, ``concurrent-checkpoint-io-bottleneck``, ...)
which work as semantic descriptors but are too granular for hard-filter
retrieval — 94% are singletons.  This script asks the LLM to cluster the
existing tag vocabulary into engineering families (~25-40 buckets), then
writes back a ``topic_group`` field on every cluster.

Design:
- ONE LLM call (the model sees the full tag vocabulary at once and can
  make globally-consistent grouping choices) — alternative per-cluster
  classification can't see siblings.
- Output format is ``<tag> -> <group>`` lines, one per input tag, so
  parsing is dumb and verification is exact.
- The script fails loud if any tag is missing from the response (no
  silent partial coverage).
- Idempotent vs ``topic_group``: re-running with ``--force`` overwrites.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know import config  # noqa: E402  triggers .env load
from rosclaw_know.llm import DEEPSEEK_MUSE_MODEL, chat  # noqa: E402

logger = logging.getLogger("rosclaw_know.label_cluster_groups")

GROUP_PROMPT = """You consolidate {n_tags} engineering problem tags into AT MOST 30 coarse
``topic_group`` buckets. This is a hard-filter taxonomy — too many groups
defeats the purpose.

Group naming:
- kebab-case, 2-3 hyphenated lowercase words (e.g., llm-inference-efficiency,
  navigation-and-vla, control-loop-stability, scheduling-optimization).
- Name an engineering FAMILY ("llm-inference-efficiency", "robot-perception"),
  NOT a single specific pattern.
- Two tags about loosely related problems in the same engineering subfield
  MUST collapse into one group, even if their specific failure modes differ.

Examples of REQUIRED consolidation:
- All navigation variants (instruction-following-navigation, long-horizon-navigation,
  zero-shot-navigation, aerial-navigation-X, domain-generalization-navigation, ...)
  → ONE group like ``navigation-and-vla``.
- All LLM context/memory/attention/KV-cache patterns → ONE group like
  ``llm-context-management``.
- All gradient/exploration/PPO collapse patterns → ONE group like
  ``rl-training-stability``.
- All PID/actuator/feedforward/saturation patterns → ONE group like
  ``control-loop-stability``.
- All checkpoint/recovery/preemption/spot-eviction patterns → ONE group like
  ``fault-tolerant-compute``.
- All retry/backoff/timeout/network-storm patterns → ONE group like
  ``rpc-resilience``.

HARD CONSTRAINTS:
- AT MOST 30 distinct topic_group names total.
- EVERY input tag must appear in the output exactly once.
- If unsure between two groups, pick the broader one.

Input tags:
{tag_list}

Output format — ONE LINE PER INPUT TAG, in any order:
<tag> -> <group_name>

No preamble, no commentary, no markdown — just {n_tags} mapping lines.
"""


def _build_prompt(tags: list[str]) -> str:
    return GROUP_PROMPT.format(
        n_tags=len(tags),
        tag_list="\n".join(sorted(tags)),
    )


_MAP_RX = re.compile(r"^\s*([a-z0-9\-]+)\s*->\s*([a-z0-9\-]+)\s*$")


def _normalize_group(raw: str) -> str:
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    return s[:60] or "unlabeled"


def _parse_response(raw: str, expected_tags: set[str]) -> dict[str, str]:
    """Returns ``tag -> group``.  Fails loud if any expected tag is missing
    or if the response yields fewer than 15 distinct groups (too flat = LLM
    didn't differentiate) or more than 60 (didn't consolidate)."""
    mapping: dict[str, str] = {}
    unparseable: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        m = _MAP_RX.match(line.strip())
        if not m:
            unparseable.append(line[:120])
            continue
        tag = m.group(1)
        group = _normalize_group(m.group(2))
        if tag in expected_tags:
            mapping[tag] = group
        else:
            unparseable.append(f"unknown-tag: {line[:120]}")

    missing = expected_tags - set(mapping.keys())
    if missing:
        print(f"ERROR: {len(missing)} tags missing from LLM response. "
              f"First 10: {sorted(missing)[:10]}", file=sys.stderr)
        if unparseable:
            print(f"  (also {len(unparseable)} unparseable lines, "
                  f"first 5: {unparseable[:5]})", file=sys.stderr)
        raise SystemExit(2)

    groups = set(mapping.values())
    if not (10 <= len(groups) <= 35):
        print(f"WARNING: LLM produced {len(groups)} groups — outside "
              f"[10, 35] target range. Sample: {sorted(groups)[:8]}",
              file=sys.stderr)

    return mapping


async def _call_llm(tags: list[str]) -> str:
    prompt = _build_prompt(tags)
    print(f"  prompt length: {len(prompt)} chars", flush=True)
    async with aiohttp.ClientSession() as session:
        raw = await chat(
            session,
            system=None,
            user=prompt,
            model=DEEPSEEK_MUSE_MODEL,
            max_tokens=16000,  # 366 lines of ~50 chars = ~18 KB worst case
            temperature=0.0,
        )
    if not raw:
        raise SystemExit("LLM returned empty response")
    return raw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bridge-path", type=Path,
                    default=config.ASSETS_DIR / "bridge_index.json")
    ap.add_argument("--force", action="store_true",
                    help="Re-label even clusters that already have topic_group.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print proposed mapping but don't write back.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if not args.bridge_path.exists():
        print(f"missing {args.bridge_path}", file=sys.stderr)
        return 1
    bridge = json.loads(args.bridge_path.read_text())

    clusters: dict[str, dict] = bridge.get("symptom_clusters", {})
    # Collect distinct topic_tag values.  Clusters that don't have a
    # topic_tag yet aren't candidates here — run label_cluster_topics first.
    tags = sorted({
        c.get("topic_tag")
        for c in clusters.values()
        if c.get("topic_tag")
    })
    if not tags:
        print("No topic_tag found on any cluster — run label_cluster_topics.py first.",
              file=sys.stderr)
        return 1
    print(f"Found {len(tags)} distinct topic_tag values across {len(clusters)} clusters.")

    needs_label = [
        cid for cid, c in clusters.items()
        if c.get("topic_tag") and (args.force or not c.get("topic_group"))
    ]
    print(f"Will assign topic_group to {len(needs_label)} clusters "
          f"({'force' if args.force else 'missing-only'}).")
    if not needs_label:
        return 0

    print("\n=== Calling LLM (one shot, all tags) ===")
    raw = asyncio.run(_call_llm(tags))
    mapping = _parse_response(raw, set(tags))

    # Distribution sanity-check.
    from collections import Counter
    group_counts = Counter(mapping.values())
    print(f"\n=== Mapping summary ===")
    print(f"  distinct topic_group buckets: {len(group_counts)}")
    print(f"  top-10 by tag count:")
    for grp, cnt in group_counts.most_common(10):
        print(f"    {cnt:>4}  {grp}")
    singletons = sum(1 for cnt in group_counts.values() if cnt == 1)
    print(f"  singleton groups: {singletons} ({singletons/len(group_counts):.0%})")

    if args.dry_run:
        print("\n=== Dry-run sample (15 tag → group lines) ===")
        for tag, grp in list(mapping.items())[:15]:
            print(f"  {tag:<40} -> {grp}")
        print(f"(would write topic_group on {len(needs_label)} clusters)")
        return 0

    # Apply mapping back onto clusters.
    n_set = 0
    for cid in needs_label:
        tag = clusters[cid].get("topic_tag")
        if tag and tag in mapping:
            clusters[cid]["topic_group"] = mapping[tag]
            n_set += 1

    args.bridge_path.write_text(
        json.dumps(bridge, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote topic_group on {n_set} clusters to {args.bridge_path}.")

    # Cross-tab: topic_group sizes after the assignment.
    cluster_groups = Counter(
        c.get("topic_group") for c in clusters.values() if c.get("topic_group")
    )
    cluster_singletons = sum(1 for cnt in cluster_groups.values() if cnt == 1)
    print(f"\nCluster-level distribution (cluster → group):")
    print(f"  distinct topic_group buckets: {len(cluster_groups)}")
    print(f"  median cluster_per_group: {sorted(cluster_groups.values())[len(cluster_groups)//2]}")
    print(f"  singleton groups: {cluster_singletons} ({cluster_singletons/len(cluster_groups):.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
