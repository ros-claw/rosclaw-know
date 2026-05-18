"""Weaver — load extracted heuristics into a NetworkX DiGraph with
cross-domain edges.

For Phase 1 the edge policy is intentionally simple: every node connects to
a bounded random sample of nodes in OTHER domains. This keeps BFS reachable
across domains without paying the O(n^2) cost the original spec would
incur on the full 6k-page corpus.
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict

import networkx as nx

from .infra import open_db

log = logging.getLogger("rosclaw_know.weaver")


def load_heuristics() -> list[dict]:
    """Read all heuristics from SQLite into memory."""
    with open_db() as conn:
        rows = conn.execute(
            "SELECT id, page_path, symptom, domain, fix_pattern, failed_attempt FROM heuristics"
        ).fetchall()
    return [dict(r) for r in rows]


def build_memory_graph(
    rows: list[dict] | None = None,
    *,
    max_cross_edges_per_node: int = 8,
    rng_seed: int = 42,
) -> nx.DiGraph:
    """Build a domain-aware in-memory graph for the Muse compiler.

    Edge semantics:
      * 'cross_domain_potential' — outgoing, target is in a DIFFERENT domain
        (random sample, capped by max_cross_edges_per_node).
    """
    rows = rows or load_heuristics()
    rng = random.Random(rng_seed)

    g = nx.DiGraph()
    by_domain: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        node_id = r["id"]
        g.add_node(
            node_id,
            symptom=r["symptom"],
            domain=r["domain"],
            fix=r.get("fix_pattern") or "",
            failed=r.get("failed_attempt") or "",
            page_path=r.get("page_path") or "",
        )
        by_domain[r["domain"]].append(node_id)

    domains = list(by_domain.keys())
    for n_id in g.nodes:
        my_domain = g.nodes[n_id]["domain"]
        other_domains = [d for d in domains if d != my_domain]
        if not other_domains:
            continue
        # Round-robin draw a few neighbours from each other domain.
        per_domain_quota = max(1, max_cross_edges_per_node // max(len(other_domains), 1))
        for d in other_domains:
            candidates = [c for c in by_domain[d] if c != n_id]
            if not candidates:
                continue
            sample = rng.sample(candidates, k=min(per_domain_quota, len(candidates)))
            for c in sample:
                g.add_edge(n_id, c, rel_type="cross_domain_potential")

    log.info(
        "Weaver: %d nodes across %d domains, %d cross-domain edges",
        g.number_of_nodes(),
        len(domains),
        g.number_of_edges(),
    )
    return g
