"""Top-level pipeline orchestrator. Wires Harvester → Weaver → Muse → Publisher."""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from . import config
from .curated_publisher import publish_curated_assets
from .harvester import run_harvester
from .infra import init_db
from .llm import get_token_usage, reset_token_usage
from .muse import compile_muse_assets
from .weaver import build_memory_graph

log = logging.getLogger("rosclaw_know.pipeline")


def collect_wiki_files(
    wiki_dir: Path | None = None,
    *,
    max_pages: int | None = None,
    shuffle: bool = True,
    seed: int = 42,
    prefer_subdirs: tuple[str, ...] | None = ("algorithms", "concepts", "entities"),
) -> list[Path]:
    """List all *.md files in the wiki, optionally truncated for first-batch runs.

    *prefer_subdirs*: when set, oversamples these subdirectories first because
    they contain more procedural-knowledge-rich pages than the ``skills/``
    directory (which is mostly MCP-server / project descriptions).
    """
    wiki_dir = wiki_dir or config.WIKI_DIR
    if not wiki_dir.exists():
        raise FileNotFoundError(f"wiki dir not found: {wiki_dir}")
    all_files = sorted(wiki_dir.rglob("*.md"))
    # Skip a few well-known meta-pages
    all_files = [f for f in all_files if f.name not in ("index.md", "log.md", "Admin_Dashboard.md")]

    rng = random.Random(seed)
    if prefer_subdirs:
        # Bucket by top-level subdirectory.
        preferred: list[Path] = []
        other: list[Path] = []
        prefer_set = set(prefer_subdirs)
        for f in all_files:
            rel = f.relative_to(wiki_dir)
            top = rel.parts[0] if rel.parts else ""
            (preferred if top in prefer_set else other).append(f)
        if shuffle:
            rng.shuffle(preferred)
            rng.shuffle(other)
        files = preferred + other
    else:
        files = list(all_files)
        if shuffle:
            rng.shuffle(files)

    if max_pages:
        files = files[:max_pages]
    return files


async def run_phase1(
    *,
    max_pages: int | None = None,
    skip_extraction: bool = False,
    skip_muse: bool = False,
    skip_curated: bool = False,
    muse_max_nodes: int | None = None,
) -> dict:
    """End-to-end Phase 1: extract → build graph → compile assets → graft curated."""
    config.ensure_dirs()
    init_db()
    reset_token_usage()

    if not config.llm_configured():
        raise RuntimeError(
            "LLM is not configured. Set DEEPSEEK_API_KEY in .env or "
            "ROSCLAW_KNOW_MOCK_LLM=1 for a dry run."
        )

    summary: dict = {}

    if not skip_extraction:
        files = collect_wiki_files(max_pages=max_pages)
        log.info("Harvesting %d wiki pages", len(files))
        harvest_stats = await run_harvester(files)
        summary["harvest"] = harvest_stats

    g = build_memory_graph()
    summary["graph_nodes"] = g.number_of_nodes()
    summary["graph_edges"] = g.number_of_edges()

    if not skip_muse:
        muse_stats = await compile_muse_assets(g, max_nodes=muse_max_nodes)
        summary["muse"] = muse_stats

    if not skip_curated:
        curated_stats = publish_curated_assets()
        summary["curated"] = curated_stats

    summary["llm_tokens"] = get_token_usage()
    return summary
