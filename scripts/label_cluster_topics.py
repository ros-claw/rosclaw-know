#!/usr/bin/env python3
"""Label each bridge_index.json cluster with a fine-grained ``topic_tag``.

Why this exists: the existing ``domain`` field (Control_Locomotion,
Systems_Compute, etc.) is too coarse to prevent the cross-task
confidently-wrong retrieval we hit on TASK_008 (job-shop scheduling
matched to a checkpoint-recovery pattern — both are Systems_Compute,
same coarse family, but the SPECIFIC engineering problem is different
and the retrieval is wrong).

This script appends ``topic_tag: <2-4 hyphenated lowercase words>`` to
every symptom_cluster entry.  The tag is meant to be machine-comparable:
two clusters share a tag iff they solve the same problem family.
rosclaw-how can later require ``query_topic_tag == cluster.topic_tag``
as a hard filter on retrieval to eliminate cross-topic false positives.

Idempotent: clusters that already have ``topic_tag`` are skipped unless
``--force`` is passed.  Writes back into the same bridge_index.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know import config  # noqa: E402  triggers .env load
from rosclaw_know.llm import DEEPSEEK_MUSE_MODEL, chat  # noqa: E402

logger = logging.getLogger("rosclaw_know.label_cluster_topics")

LABEL_PROMPT = """You assign a precise topic_tag to engineering patterns.

Output ONE topic_tag for the pattern below.  Rules:
- 2-4 lowercase words joined by hyphens (e.g., "kv-cache-management",
  "rare-event-sampling", "pid-anti-windup", "checkpoint-recovery",
  "topology-optimization-checkerboard", "actuator-output-saturation").
- SPECIFIC, not general: prefer "kv-cache-management" over "memory"
  or "context-window".
- Name the engineering PROBLEM, not the solution technique:
  "rare-event-sampling" (problem) not "monte-carlo" (technique).
- Two patterns that solve the same problem family should get the
  same tag.  Two patterns in the same domain but addressing
  different problems should get DIFFERENT tags.

Pattern metadata:
  domain: {domain}
  standard_name: {standard_name}
  matched_keywords: {keywords}
  first_analogy_insight: {analogy}

Output the tag on a single line, no surrounding quotes, no preamble.
"""


def _build_user_prompt(cluster_id: str, cluster: dict) -> str:
    analogies = cluster.get("cross_domain_analogies") or []
    first_insight = ""
    if analogies and isinstance(analogies[0], dict):
        first_insight = str(analogies[0].get("insight", ""))[:240]
    return LABEL_PROMPT.format(
        domain=cluster.get("domain", "?"),
        standard_name=str(cluster.get("standard_name", cluster_id))[:240],
        keywords=cluster.get("matched_keywords", [])[:10],
        analogy=first_insight or "(no cross-domain analogy recorded)",
    )


def _normalize_tag(raw: str) -> str:
    """Sanitize the LLM's output: strip quotes, lowercase, collapse runs."""
    import re
    s = raw.strip().strip("\"' `").lower()
    # drop anything after first newline (LLM occasionally adds explanation)
    s = s.split("\n", 1)[0]
    # only keep [a-z0-9-] characters; collapse runs of -
    s = re.sub(r"[^a-z0-9\-]+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    # clip length to keep file diff readable
    return s[:60] or "unlabeled"


async def _label_one(
    session: aiohttp.ClientSession,
    *,
    cluster_id: str,
    cluster: dict,
) -> str:
    raw = await chat(
        session,
        None,
        _build_user_prompt(cluster_id, cluster),
        model=DEEPSEEK_MUSE_MODEL,
        max_tokens=40,
        temperature=0.0,
    )
    if not raw:
        return "unlabeled"
    return _normalize_tag(raw)


async def _label_all(
    bridge: dict,
    *,
    force: bool,
    concurrency: int,
) -> dict[str, str]:
    """Returns ``cluster_id -> topic_tag``, only for clusters that were labeled
    (skipped ones don't appear)."""
    clusters: dict[str, dict] = bridge.get("symptom_clusters", {})
    targets = [
        (cid, c) for cid, c in clusters.items()
        if force or not c.get("topic_tag")
    ]
    print(f"Need to label {len(targets)} of {len(clusters)} clusters "
          f"({'force-relabel' if force else 'missing topic_tag'}).", flush=True)
    if not targets:
        return {}

    sem = asyncio.Semaphore(concurrency)
    results: dict[str, str] = {}

    async def run_one(cid: str, c: dict) -> None:
        async with sem:
            tag = await _label_one(session, cluster_id=cid, cluster=c)
            results[cid] = tag
            done = len(results)
            if done % 25 == 0 or done == len(targets):
                print(f"  labeled {done}/{len(targets)}", flush=True)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(run_one(cid, c) for cid, c in targets))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bridge-path", type=Path,
                    default=config.ASSETS_DIR / "bridge_index.json")
    ap.add_argument("--force", action="store_true",
                    help="Re-label even clusters that already have topic_tag.")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="Concurrent LLM calls (DeepSeek can sustain ~10).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print proposed tags but don't write back.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    bridge_path: Path = args.bridge_path
    if not bridge_path.exists():
        print(f"missing {bridge_path}", file=sys.stderr)
        return 1
    bridge = json.loads(bridge_path.read_text())

    new_tags = asyncio.run(_label_all(
        bridge, force=args.force, concurrency=args.concurrency,
    ))

    if args.dry_run:
        print("\n=== Dry-run: proposed tags (sample of 30) ===")
        for cid, tag in list(new_tags.items())[:30]:
            print(f"  {cid[:50]:<50}  → {tag}")
        print(f"(total {len(new_tags)} would be written)")
        return 0

    # Write the new tags into the bridge entries.
    clusters: dict[str, dict] = bridge["symptom_clusters"]
    for cid, tag in new_tags.items():
        clusters[cid]["topic_tag"] = tag

    bridge_path.write_text(
        json.dumps(bridge, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {len(new_tags)} new topic_tag entries to {bridge_path}.")

    # Print a frequency distribution so we can eyeball coverage.
    from collections import Counter
    all_tags = [c.get("topic_tag", "unlabeled") for c in clusters.values()]
    top = Counter(all_tags).most_common(20)
    print("\nTop-20 topic_tag frequencies:")
    for tag, cnt in top:
        print(f"  {cnt:>4}  {tag}")
    print(f"\nTotal distinct topic_tags: {len(set(all_tags))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
