"""Harvester — async LLM extraction of (symptom, fix_pattern) from wiki pages.

Stage 2 of the four-stage Know pipeline. Concurrency is bounded by an asyncio
semaphore; resumability is achieved with MD5 fingerprinting of the page text.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

import aiohttp
from tqdm.asyncio import tqdm as atqdm

from .ast_extract import extract_ast_functions
from .config import HARVESTER_CONCURRENCY
from .infra import (
    is_processed,
    mark_processed,
    open_db,
    upsert_heuristic,
)
from .llm import DEEPSEEK_EXTRACTOR_MODEL, chat_json
from .prompts import FRONTIER_DOMAINS, extractor_prompt_for
from .seekdb_align import check_duplicate_and_align

log = logging.getLogger("rosclaw_know.harvester")

# Strip YAML frontmatter when present.
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
# Frontmatter scalar field — k: v (one per line, no nesting).
_FRONTMATTER_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Best-effort extract of scalar fields from a YAML-style frontmatter block.

    Used to read ``source_type`` (or its alias ``fetch_kind``) so the
    extractor prompt can be specialised per source. Returns {} if the
    document has no frontmatter.
    """
    if not text.startswith("---"):
        return {}
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for field_match in _FRONTMATTER_FIELD.finditer(m.group(1)):
        key = field_match.group(1).strip().lower()
        # Strip inline comments and surrounding quotes.
        val = field_match.group(2).split("#", 1)[0].strip().strip('"').strip("'")
        out[key] = val
    return out


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1) if text.startswith("---") else text


def _looks_useful(text: str) -> bool:
    """Cheap filter — skip pages that obviously have no procedural content."""
    if len(text.strip()) < 200:
        return False
    return True


async def process_single_page(
    semaphore: asyncio.Semaphore,
    file_path: Path,
    session: aiohttp.ClientSession,
    *,
    db_lock: asyncio.Lock,
) -> dict | None:
    """Run the harvester pipeline on one file. Returns the heuristic row or None."""
    async with semaphore:
        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("read failed for %s: %s", file_path, exc)
            return None

        body = _strip_frontmatter(raw_text)
        if not _looks_useful(body):
            return None

        # Phase 10: pick the extractor prompt based on the source type
        # declared in frontmatter (set by awesome_fetcher / research_sources).
        fm = _parse_frontmatter(raw_text)
        source_type = fm.get("source_type") or fm.get("fetch_kind")
        extractor_prompt = extractor_prompt_for(source_type)

        file_md5 = hashlib.md5(body.encode("utf-8")).hexdigest()

        # Resumability — short-circuit on already-processed
        async with db_lock:
            with open_db() as conn:
                if is_processed(conn, file_md5):
                    return None

        content = body[:3000]
        ast_context = extract_ast_functions(content) if "```python" in content or ".py" in content else ""
        combined = f"{content}\n{ast_context}" if ast_context else content

        result = await chat_json(
            session,
            extractor_prompt,
            combined,
            model=DEEPSEEK_EXTRACTOR_MODEL,
            max_tokens=500,
            temperature=0.0,
        )
        if result is None:
            async with db_lock:
                with open_db() as conn:
                    mark_processed(conn, file_md5, str(file_path), "llm_error")
                    conn.commit()
            return None

        symptom = (result.get("symptom") or "").strip()
        if not symptom or symptom.lower() == "null":
            async with db_lock:
                with open_db() as conn:
                    mark_processed(conn, file_md5, str(file_path), "skipped_no_symptom")
                    conn.commit()
            return None

        domain = (result.get("domain") or "").strip()
        if domain not in FRONTIER_DOMAINS:
            domain = "Planning_Decision"  # broad catch-all in the embodied-AI taxonomy

        align = check_duplicate_and_align(
            symptom,
            domain,
            (result.get("fix_pattern") or "").strip(),
        )

        async with db_lock:
            with open_db() as conn:
                if align["action"] == "skip":
                    mark_processed(conn, file_md5, str(file_path), "skipped_dup")
                    conn.commit()
                    return None
                mark_processed(conn, file_md5, str(file_path), "extracted")
                conn.commit()

        node_id = (
            align["existing_id"] if align["action"] == "merge" else file_path.stem
        )
        return {
            "id": node_id,
            "page_path": str(file_path),
            "symptom": symptom,
            "domain": domain,
            "fix_pattern": (result.get("fix_pattern") or "").strip(),
            "failed_attempt": (result.get("failed_attempt") or "").strip(),
            "raw_content": body,
            "merged": align["action"] == "merge",
        }


async def run_harvester(
    md_files: Sequence[Path] | Iterable[Path],
    *,
    concurrency: int | None = None,
) -> dict[str, int]:
    """Run the harvester over a list of markdown files.

    Returns a stats dict with counts.
    """
    md_files = list(md_files)
    semaphore = asyncio.Semaphore(concurrency or HARVESTER_CONCURRENCY)
    db_lock = asyncio.Lock()

    counts = {"input": len(md_files), "extracted": 0, "skipped": 0, "errors": 0}

    async with aiohttp.ClientSession() as session:
        coros = [
            process_single_page(semaphore, fp, session, db_lock=db_lock)
            for fp in md_files
        ]
        for fut in atqdm.as_completed(coros, total=len(coros), desc="harvest"):
            res = await fut
            if res is None:
                counts["skipped"] += 1
                continue
            async with db_lock:
                with open_db() as conn:
                    upsert_heuristic(conn, res)
                    conn.commit()
            counts["extracted"] += 1

    return counts
