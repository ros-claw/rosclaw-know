"""Muse compiler — BFS cross-domain analogies + Unified-Diff code patterns.

Final stage of the Know pipeline. Reads the in-memory NetworkX graph,
asks the LLM to translate each (symptom_a, fix_a, symptom_b) tuple into a
one-sentence analogy, and emits two artifacts:

    data/assets/bridge_index.json
    data/assets/code_patterns/pattern_<id>.md

These are the deliverables that rosclaw-how will load into SeekDB at boot.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from pathlib import Path

import aiohttp
import networkx as nx

from . import config
from .llm import DEEPSEEK_MUSE_MODEL, chat
from .prompts import MUSE_PROMPT

log = logging.getLogger("rosclaw_know.muse")

# Domain-rooted vocabulary used to build matched_keywords. The runtime
# embeds (symptom + keywords) — adding domain-canonical words improves the
# similarity score when the agent's normalized error label matches.
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Perception_Vision": ("perception", "vision", "depth", "segmentation", "feature", "image"),
    "Planning_Decision": ("planning", "navigation", "policy", "action", "decision", "trajectory"),
    "Control_Locomotion": ("control", "locomotion", "pid", "actuator", "torque", "gait", "balance"),
    "Learning_Training": ("learning", "training", "policy", "reinforcement", "imitation", "fine-tune"),
    "Memory_Reasoning": ("memory", "reasoning", "context", "kv-cache", "chain-of-thought", "recall"),
    "Systems_Compute": ("systems", "compute", "cuda", "gpu", "latency", "throughput", "scheduling"),
    "World_Physics": ("simulation", "physics", "contact", "dynamics", "friction", "mesh"),
}

# Very small stop-list — words too generic to help vector search.
_KEYWORD_STOPLIST: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "this", "that", "they", "their",
        "are", "was", "were", "but", "not", "into", "than", "then", "have",
        "has", "had", "such", "also", "any", "all", "can", "may", "use",
        "using", "used", "via", "due", "to", "of", "in", "on", "at", "by",
        "or", "is", "be", "an", "as", "it",
    }
)


def _extract_meaningful_keywords(symptom: str, domain: str, limit: int = 8) -> list[str]:
    """Pick keyword candidates from the symptom plus domain canonicals.

    Heuristic: take non-stoplist words ≥4 chars (preserves identifiers like
    "vln", "pid", "rl" via the domain canonical list), de-dup, append the
    canonical domain vocabulary at the tail.
    """
    raw = re.findall(r"[A-Za-z][A-Za-z\-_]+", symptom.lower())
    picked: list[str] = []
    seen: set[str] = set()
    for w in raw:
        if w in seen or w in _KEYWORD_STOPLIST or len(w) < 4:
            continue
        picked.append(w)
        seen.add(w)
        if len(picked) >= limit - 2:  # leave room for two domain canonicals
            break
    for canon in _DOMAIN_KEYWORDS.get(domain, ()):
        if canon not in seen:
            picked.append(canon)
            seen.add(canon)
            if len(picked) >= limit:
                break
    return picked


def _id_to_slug(node_id: str) -> str:
    """Filesystem-safe slug from a node id."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", node_id)
    return s.strip("_")[:80] or "pattern"


def _make_unified_diff(node_id: str, symptom: str, fix_text: str, failed: str) -> str:
    """Build a synthetic Unified Diff that records the fix as an instructive
    code-comment graft. Real diffs against actual codebases will come later
    when Frontier-Engineering hooks are wired in.
    """
    before = [
        "# --- BEFORE (vulnerable to the symptom below) ---\n",
        f"# Symptom: {symptom}\n",
    ]
    after = [
        "# --- AFTER (ROSCLAW heuristic graft) ---\n",
        f"# Symptom: {symptom}\n",
        f"# Fix    : {fix_text}\n",
    ]
    if failed:
        after.append(f"# Avoid  : {failed}\n")
    diff = difflib.unified_diff(
        before,
        after,
        fromfile=f"{node_id}.before.py",
        tofile=f"{node_id}.after.py",
        lineterm="",
    )
    return "\n".join(diff)


async def _generate_analogy(
    session: aiohttp.ClientSession,
    *,
    domain_a: str,
    symptom_a: str,
    fix_a: str,
    domain_b: str,
    symptom_b: str,
) -> str | None:
    prompt = MUSE_PROMPT.format(
        domain_a=domain_a,
        symptom_a=symptom_a,
        fix_a=fix_a,
        domain_b=domain_b,
        symptom_b=symptom_b,
    )
    raw = await chat(
        session,
        None,
        prompt,
        model=DEEPSEEK_MUSE_MODEL,
        max_tokens=180,
        temperature=0.4,
    )
    if not raw:
        return None
    return raw.strip().strip('"')


def _write_pattern_file(
    node_id: str,
    attr: dict,
    analogies: list[dict],
) -> Path:
    slug = _id_to_slug(node_id)
    out_path = config.CODE_PATTERNS_DIR / f"pattern_{slug}.md"
    diff = _make_unified_diff(
        node_id, attr["symptom"], attr["fix"], attr.get("failed") or ""
    )
    body = []
    body.append("---")
    body.append(f"pattern_id: pattern_{slug}")
    body.append(f"applicable_symptoms: [{node_id}]")
    body.append(f"domain: {attr['domain']}")
    body.append("---")
    body.append("")
    body.append(f"# {attr['symptom']}")
    body.append("")
    body.append(f"**Domain**: `{attr['domain']}`")
    body.append("")
    body.append("## Fix")
    body.append("")
    body.append(attr["fix"] or "_(no fix recorded)_")
    body.append("")
    if attr.get("failed"):
        body.append("## Anti-pattern")
        body.append("")
        body.append(attr["failed"])
        body.append("")
    if analogies:
        body.append("## Cross-domain analogies")
        body.append("")
        for a in analogies:
            body.append(f"- **{a['source_domain']}** → {a['insight']}")
            body.append(f"  - related fix: {a['action_suggestion']}")
        body.append("")
    body.append("## Patch")
    body.append("")
    body.append("```diff")
    body.append(diff)
    body.append("```")
    out_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return out_path


async def compile_muse_assets(
    g: nx.DiGraph,
    *,
    bfs_depth: int = 2,
    max_analogies_per_node: int = 3,
    max_nodes: int | None = None,
    concurrency: int = 8,
) -> dict:
    """Run the Muse compiler and emit assets.

    LLM calls within a single node are sequential (we need each to commit
    or skip before deciding to try the next analogy), but per-node work is
    fanned out via a semaphore so 8 nodes proceed in parallel.

    Returns a summary dict with counts and paths.
    """
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    config.CODE_PATTERNS_DIR.mkdir(parents=True, exist_ok=True)

    # Wipe previous Muse output so the code_patterns/ dir stays in 1-to-1
    # sync with bridge_index.json. Curated patterns (named without the
    # ``pattern_`` prefix — see curated_publisher.py) survive the wipe and
    # get re-published downstream.
    for existing in config.CODE_PATTERNS_DIR.glob("pattern_*.md"):
        try:
            existing.unlink()
        except OSError:
            pass

    nodes = list(g.nodes)
    if max_nodes:
        nodes = nodes[:max_nodes]

    bridge_index: dict[str, dict] = {"symptom_clusters": {}}
    written_patterns: list[str] = []
    semaphore = asyncio.Semaphore(concurrency)
    bridge_lock = asyncio.Lock()

    async def process_node(session: aiohttp.ClientSession, node: str) -> None:
        async with semaphore:
            attr = g.nodes[node]
            symptom = attr["symptom"]
            domain = attr["domain"]

            try:
                horizon = list(nx.bfs_tree(g, source=node, depth_limit=bfs_depth))
            except nx.NetworkXError:
                return
            cross_pool = [
                n for n in horizon if n != node and g.nodes[n].get("domain") != domain
            ]
            if not cross_pool:
                return

            analogies: list[dict] = []
            for neighbor in cross_pool:
                if len(analogies) >= max_analogies_per_node:
                    break
                neigh_attr = g.nodes[neighbor]
                insight = await _generate_analogy(
                    session,
                    domain_a=neigh_attr["domain"],
                    symptom_a=neigh_attr["symptom"],
                    fix_a=neigh_attr.get("fix", ""),
                    domain_b=domain,
                    symptom_b=symptom,
                )
                if not insight:
                    continue
                analogies.append(
                    {
                        "source_domain": neigh_attr["domain"],
                        "neighbor_id": neighbor,
                        "insight": insight,
                        "action_suggestion": neigh_attr.get("fix", ""),
                    }
                )

            if not analogies:
                return

            slug = _id_to_slug(node)
            entry = {
                "standard_name": symptom,
                "domain": domain,
                "matched_keywords": _extract_meaningful_keywords(symptom, domain),
                "cross_domain_analogies": analogies,
                "associated_patterns": [f"pattern_{slug}"],
            }
            pattern_path = _write_pattern_file(node, attr, analogies)
            rel = str(pattern_path.relative_to(config.ASSETS_DIR.parent))
            async with bridge_lock:
                bridge_index["symptom_clusters"][node] = entry
                written_patterns.append(rel)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(process_node(session, n) for n in nodes))

    bridge_path = config.ASSETS_DIR / "bridge_index.json"
    bridge_path.write_text(
        json.dumps(bridge_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "Muse: %d symptom clusters, %d code patterns written",
        len(bridge_index["symptom_clusters"]),
        len(written_patterns),
    )
    return {
        "clusters": len(bridge_index["symptom_clusters"]),
        "patterns": len(written_patterns),
        "bridge_path": str(bridge_path),
    }


# Synchronous convenience wrapper for scripts
def compile_muse_assets_sync(g: nx.DiGraph, **kwargs) -> dict:
    return asyncio.run(compile_muse_assets(g, **kwargs))
