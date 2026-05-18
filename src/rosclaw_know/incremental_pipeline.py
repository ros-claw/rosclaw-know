"""Incremental ingest pipeline — add wiki content without re-running Phase 1.

Phase 5 needs a way to grow the knowledge base in place: new papers and code
articles drop into the wiki, the manifest records what's new, the harvester
extracts only the dirty files, the weaver rebuilds the graph, and Muse runs
*only* on clusters that did not already exist in ``bridge_index.json``.

Why selective Muse? It's the only LLM-heavy step. Full Phase 1 (~80 nodes
× 3 analogies × ~$0.005 each) cost about 0.6 RMB; doing it every time we
add a paper would be wasteful and would also clobber Phase 4's feedback
metrics (``uplift_mean``, ``win_rate``, ``priority``) that the
:mod:`bridge_reweighter` writes back into the same JSON file.

Preserved fields on merge:

  * The whole curated ``safety_label_index`` block (curated patterns own it).
  * Per-cluster Phase 4 stats: ``uplift_mean``, ``uplift_n``, ``win_rate``,
    ``priority``.
  * Any cluster the user manually edited (only new clusters get rewritten).

What this module does NOT do:

  * Strip stale clusters whose source files vanished. That's
    :func:`scripts.lint_bridge.report_orphans` territory.
  * Touch ``code_patterns/*.md`` for existing clusters. We only write new
    files for new clusters.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Iterable

import aiohttp
import networkx as nx

from . import config
from .harvester import run_harvester
from .muse import (
    _extract_meaningful_keywords,
    _generate_analogy,
    _id_to_slug,
    _write_pattern_file,
)
from .source_manifest import SourceManifest
from .weaver import build_memory_graph

log = logging.getLogger("rosclaw_know.incremental_pipeline")


def _load_existing_bridge() -> dict:
    bridge_path = config.ASSETS_DIR / "bridge_index.json"
    if not bridge_path.exists():
        return {"symptom_clusters": {}}
    try:
        return json.loads(bridge_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Existing bridge_index unreadable, starting fresh: %s", exc)
        return {"symptom_clusters": {}}


def _write_bridge(bridge: dict) -> Path:
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    bridge_path = config.ASSETS_DIR / "bridge_index.json"
    bridge_path.write_text(
        json.dumps(bridge, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return bridge_path


async def _muse_node(
    session: aiohttp.ClientSession,
    g: nx.DiGraph,
    node: str,
    *,
    bfs_depth: int,
    max_analogies: int,
) -> dict | None:
    """Run Muse for a single node. Returns the new cluster dict or None."""
    attr = g.nodes[node]
    symptom = attr["symptom"]
    domain = attr["domain"]

    try:
        horizon = list(nx.bfs_tree(g, source=node, depth_limit=bfs_depth))
    except nx.NetworkXError:
        return None
    cross_pool = [
        n for n in horizon if n != node and g.nodes[n].get("domain") != domain
    ]
    if not cross_pool:
        return None

    analogies: list[dict] = []
    for neighbor in cross_pool:
        if len(analogies) >= max_analogies:
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
        analogies.append({
            "source_domain": neigh_attr["domain"],
            "neighbor_id": neighbor,
            "insight": insight,
            "action_suggestion": neigh_attr.get("fix", ""),
        })
    if not analogies:
        return None

    slug = _id_to_slug(node)
    _write_pattern_file(node, attr, analogies)
    return {
        "standard_name": symptom,
        "domain": domain,
        "matched_keywords": _extract_meaningful_keywords(symptom, domain),
        "cross_domain_analogies": analogies,
        "associated_patterns": [f"pattern_{slug}"],
    }


async def compile_muse_incremental(
    g: nx.DiGraph,
    new_node_ids: Iterable[str],
    *,
    bfs_depth: int = 2,
    max_analogies_per_node: int = 3,
    concurrency: int = 8,
) -> dict[str, dict]:
    """Run Muse on the given nodes only, returning ``node_id -> cluster_entry``.

    Existing cluster entries are not touched. The caller merges these into
    the bridge_index.
    """
    config.CODE_PATTERNS_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    targets = list(new_node_ids)
    results: dict[str, dict] = {}

    async def run_one(session: aiohttp.ClientSession, node: str) -> None:
        async with sem:
            entry = await _muse_node(
                session, g, node,
                bfs_depth=bfs_depth,
                max_analogies=max_analogies_per_node,
            )
            if entry is not None:
                results[node] = entry

    if not targets:
        log.info("Incremental Muse: nothing to do (0 target nodes)")
        return results

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(run_one(session, n) for n in targets))
    log.info(
        "Incremental Muse: produced %d new clusters from %d candidate nodes",
        len(results), len(targets),
    )
    return results


def merge_into_bridge(new_clusters: dict[str, dict]) -> dict[str, int]:
    """Merge ``new_clusters`` into bridge_index.json non-destructively.

    Returns stats: ``{"added": N, "skipped_existing": N, "total": N}``.
    Existing cluster entries (curated, prior Muse, or Phase 4-decorated)
    are NOT overwritten — only brand-new ids get inserted.
    """
    bridge = _load_existing_bridge()
    clusters = bridge.setdefault("symptom_clusters", {})
    added = 0
    skipped = 0
    for node_id, entry in new_clusters.items():
        if node_id in clusters:
            skipped += 1
            continue
        clusters[node_id] = entry
        added += 1

    if added:
        _write_bridge(bridge)
        log.info("Bridge merge: %d new clusters added (total now %d)", added, len(clusters))
    return {"added": added, "skipped_existing": skipped, "total": len(clusters)}


def _gather_candidate_paths(paths: Iterable[Path]) -> list[Path]:
    """Resolve user-provided paths into a flat list of markdown files."""
    out: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_dir():
            out.extend(sorted(q for q in p.rglob("*.md") if q.is_file()))
        elif p.is_file() and p.suffix == ".md":
            out.append(p)
        else:
            log.warning("Ignoring non-markdown path %s", p)
    return out


async def run_incremental_ingest(
    new_paths: Iterable[Path],
    *,
    manifest_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Top-level entry: ingest ``new_paths`` end-to-end.

    Steps:
      1. Resolve to markdown files.
      2. Filter to dirty (new / changed) via SourceManifest.
      3. Harvest only the dirty subset.
      4. Re-weave the full graph (cheap — NetworkX in-memory).
      5. Run Muse incrementally on graph nodes NOT in bridge_index yet.
      6. Merge new clusters into bridge_index.
      7. Update the manifest.

    ``dry_run=True`` skips harvest + Muse — useful for previewing what
    would be processed.
    """
    if not config.llm_configured():
        raise RuntimeError(
            "LLM not configured. Set DEEPSEEK_API_KEY or ROSCLAW_KNOW_MOCK_LLM=1"
        )
    config.ensure_dirs()

    manifest = SourceManifest.load(manifest_path)
    candidates = _gather_candidate_paths(new_paths)
    dirty = manifest.select_dirty(candidates)

    summary: dict = {
        "candidates_total": len(candidates),
        "dirty_total": len(dirty),
        "dirty_files": [(str(p), s) for p, s in dirty],
    }
    if dry_run:
        summary["dry_run"] = True
        return summary

    if dirty:
        harvest_stats = await run_harvester([p for p, _ in dirty])
        summary["harvest"] = harvest_stats
        for p, _status in dirty:
            manifest.upsert(p)
    else:
        log.info("No dirty files; skipping harvester")

    g = build_memory_graph()
    summary["graph_nodes"] = g.number_of_nodes()
    summary["graph_edges"] = g.number_of_edges()

    existing_ids = set(_load_existing_bridge().get("symptom_clusters", {}).keys())
    new_graph_ids = [n for n in g.nodes if n not in existing_ids]
    summary["new_graph_nodes"] = len(new_graph_ids)

    new_clusters = await compile_muse_incremental(g, new_graph_ids)
    summary["muse"] = {"new_clusters_minted": len(new_clusters)}

    merge_stats = merge_into_bridge(new_clusters)
    summary["bridge_merge"] = merge_stats

    # Bookkeeping
    for p, _status in dirty:
        manifest.record_contribution(p, n_extra_clusters=0)  # cluster attribution TBD
    if not manifest_path:
        manifest_path = manifest.path
    manifest.save()
    summary["manifest_path"] = str(manifest_path)
    return summary


__all__ = [
    "compile_muse_incremental",
    "merge_into_bridge",
    "run_incremental_ingest",
]
