#!/usr/bin/env python3
"""scripts/snapshot_stats.py — bake current runtime stats into a JSON.

Produces ``data/assets/_runtime_stats.json``, a small file an AI agent
or human deployer can read INSTEAD of running the weaver themselves. Run
this after any significant change to bridge_index.json:

  - after `python scripts/run_phase1.py`
  - after `python scripts/ingest.py`
  - after `python scripts/ingest_awesome.py --then-ingest`
  - after `python scripts/distill_feedback.py && python scripts/reweight_bridge.py`

Output schema:

    {
      "generated_at": ISO-8601,
      "bridge_clusters": int,
      "graph_nodes": int,
      "graph_edges": int,
      "domains": {domain_name: count, ...},
      "lifecycle": {staging: int, production: int, demoted: int, unbucketed: int},
      "pattern_files": int
    }

The file is committed so a fresh clone has authoritative numbers without
needing to install dependencies or run the LLM-heavy pipeline.
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import ASSETS_DIR, CODE_PATTERNS_DIR  # noqa: E402

logger = logging.getLogger("rosclaw_know.snapshot_stats")


def _bridge_stats() -> dict:
    bridge_path = ASSETS_DIR / "bridge_index.json"
    data = json.loads(bridge_path.read_text(encoding="utf-8"))
    clusters = data.get("symptom_clusters", {})
    domains: Counter[str] = Counter()
    lifecycle = {"staging": 0, "production": 0, "demoted": 0, "unbucketed": 0}
    for c in clusters.values():
        domains[c.get("domain", "(unknown)")] += 1
        p = c.get("priority")
        if p == 0:
            lifecycle["staging"] += 1
        elif p == 1:
            lifecycle["production"] += 1
        elif p == -1:
            lifecycle["demoted"] += 1
        else:
            lifecycle["unbucketed"] += 1
    return {
        "bridge_clusters": len(clusters),
        "domains": dict(domains),
        "lifecycle": lifecycle,
    }


def _graph_stats() -> dict:
    """Optional — only if the weaver can build a graph in this environment."""
    try:
        from rosclaw_know.weaver import build_memory_graph
        g = build_memory_graph()
        return {
            "graph_nodes": g.number_of_nodes(),
            "graph_edges": g.number_of_edges(),
        }
    except Exception as exc:  # noqa: BLE001 — fail soft on fresh clones
        logger.warning("Skipping graph stats (weaver unavailable): %s", exc)
        return {"graph_nodes": None, "graph_edges": None}


def _pattern_file_count() -> int:
    if not CODE_PATTERNS_DIR.exists():
        return 0
    return sum(1 for p in CODE_PATTERNS_DIR.iterdir() if p.suffix == ".md")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        **_bridge_stats(),
        **_graph_stats(),
        "pattern_files": _pattern_file_count(),
    }
    out = ASSETS_DIR / "_runtime_stats.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
