"""Active learning — draft new wiki sources for blind-spots automatically.

Phase 7 closes the meta-loop:

  agents query /build → some symptoms miss → /wiki/v1/blind_spots tracks
  the high-frequency prefixes → active_learning fetches them, calls
  DeepSeek to draft a markdown filling the gap, runs ingest, /admin/reload.

Drafted clusters land with ``priority=0`` (staging) so they're audible to
agents but the operator can still suppress them via :doc:`scripts/promote`
once feedback comes in.

The autodraft prompt is intentionally conservative:
  * Ask the model for ONE specific symptom (not a survey).
  * Reuse the same markdown shape the harvester already parses
    (anti-pattern / fix / cross-domain context blocks).
  * Stamp ``autodrafted_from: <blind_spot_prefix_hash>`` in front-matter so
    a future audit can trace the cluster back to its evidence.

We do NOT call this in a hot loop — it's a batch job driven from cron / a
manual ``scripts/autodraft.py`` invocation. The blind-spot HTTP API does
the buffering; we just ask "what's worth drafting today?".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from . import config
from .llm import chat as deepseek_chat

logger = logging.getLogger("rosclaw_know.active_learning")

# Where drafted markdowns land. Lives under wiki/ so the harvester picks
# them up like any other source.
AUTO_DRAFT_DIR = config.WIKI_DIR / "auto_drafted"

# Pull from this many spots per run; keep the autodraft fan-out bounded.
MAX_DRAFTS_PER_RUN = 5
MIN_SAMPLE_THRESHOLD = 5

DRAFT_SYSTEM_PROMPT = (
    "You are a robotics-engineering wiki editor. Given an error symptom that the "
    "knowledge base currently CANNOT match, draft a single self-contained "
    "Markdown wiki page that describes: (1) the symptom in one paragraph, "
    "(2) the most likely root cause, (3) a concrete code-level fix (Python "
    "preferred; show before+after), (4) the anti-pattern to avoid, "
    "(5) one cross-domain analogy. Stay within 600 words. Do NOT invent "
    "library names that don't exist. Output ONLY the markdown body — no "
    "wrapping fence or explanation."
)


def _build_user_prompt(blind_spot: dict[str, Any]) -> str:
    samples = blind_spot.get("samples", []) or []
    sample_text = "\n".join(f"- {s}" for s in samples[:5])
    related = blind_spot.get("related_existing_clusters", []) or []
    related_text = "\n".join(f"- {r}" for r in related[:5]) or "(none nearby)"
    return (
        f"Blind-spot prefix-hash: {blind_spot.get('prefix_hash', 'unknown')}\n"
        f"Recent occurrences: {blind_spot.get('count', '?')}\n\n"
        f"Sample error logs:\n{sample_text}\n\n"
        f"Existing clusters that look superficially related "
        f"(treat as out-of-distribution — they did NOT match well):\n"
        f"{related_text}\n\n"
        f"Draft the wiki page now."
    )


async def _draft_one(blind_spot: dict[str, Any], session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Call DeepSeek to draft a markdown body for this blind-spot.

    Returns ``(prefix_hash, markdown_body)`` or ``None`` on failure.
    """
    if config.MOCK_LLM:
        # Deterministic stub for offline tests. Must be ≥200 chars after
        # frontmatter stripping so the harvester's _looks_useful() filter
        # doesn't drop it, and rich enough for the extractor LLM to yield
        # a symptom + fix_pattern the weaver can turn into a cluster.
        prefix_hash = str(blind_spot.get("prefix_hash", "mock"))
        samples = blind_spot.get("samples") or ["unknown error log"]
        sample = samples[0] if isinstance(samples, list) else str(samples)
        return prefix_hash, (
            f"# Symptom: Quantum Simulator Decoherence During Variational Optimisation\n\n"
            f"## Problem Description\n\n"
            f"When running quantum approximate optimisation algorithm (QAOA) circuits "
            f"on noisy intermediate-scale quantum (NISQ) hardware, state-vector fidelity "
            f"degrades monotonically as gate depth increases. After approximately 20 layers, "
            f"the convergence plateau drops below the classical benchmark, making the "
            f"quantum advantage vanish. Error-mitigation techniques such as zero-noise "
            f"extrapolation help marginally but do not restore the original convergence curve.\n\n"
            f"## Representative Error Log\n\n"
            f"```\n{sample[:200]}\n```\n\n"
            f"## Fix Pattern\n\n"
            f"Reduce the Trotter step size by a factor of 5 (e.g. from 100 steps to 20 steps) "
            f"and compensate by increasing the number of measurement shots per circuit evaluation "
            f"from 1024 to 4096. This trades circuit depth for statistical precision, which is "
            f"the correct optimisation axis on current NISQ devices where gate error dominates "
            f"over shot noise. Additionally, apply dynamical decoupling sequences between active "
            f"gates to suppress idle-time decoherence.\n\n"
            f"## Cross-Domain Analogies\n\n"
            f"- **Classical Optimisation:** Smaller learning rate plus more epochs stabilises "
            f"  stochastic gradient descent when gradient noise is high.\n"
            f"- **Robotics Control:** Reducing control horizon H and increasing sampling frequency "
            f"  recovers MPC stability when actuator latency is non-negligible.\n\n"
            f"## Anti-Patterns to Avoid\n\n"
            f"- Blindly increasing circuit depth hoping for better approximation ratios.\n"
            f"- Using the same shot budget regardless of circuit depth.\n"
        )
    try:
        body = await deepseek_chat(
            session,
            system=DRAFT_SYSTEM_PROMPT,
            user=_build_user_prompt(blind_spot),
            model=config.DEEPSEEK_EXTRACTOR_MODEL,
            temperature=0.2,
            max_tokens=900,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Autodraft failed for %s: %s", blind_spot.get("prefix_hash"), exc)
        return None
    if not body or len(body.strip()) < 100:
        logger.warning("Autodraft for %s produced empty / too-short body", blind_spot.get("prefix_hash"))
        return None
    return str(blind_spot.get("prefix_hash") or "unknown"), body


def _write_draft(prefix_hash: str, body: str, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or AUTO_DRAFT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_hash = hashlib.sha1(prefix_hash.encode("utf-8")).hexdigest()[:10]
    out = out_dir / f"{stamp}_{safe_hash}.md"
    frontmatter = (
        "---\n"
        f"autodrafted_from: {prefix_hash}\n"
        f"drafted_at: {datetime.now(timezone.utc).isoformat()}\n"
        "phase: 7-active-learning\n"
        "priority: 0   # staging — review before promotion\n"
        "---\n\n"
    )
    out.write_text(frontmatter + body, encoding="utf-8")
    return out


def fetch_blind_spots(url: str = "http://127.0.0.1:47820/wiki/v1/blind_spots") -> list[dict[str, Any]]:
    """GET /wiki/v1/blind_spots — no auth required (read-only)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.warning("Could not fetch blind_spots from %s: %s", url, exc)
        return []
    # Tolerate Phase 6 #49 nested shape, plain {prefix: count} map, or list shape.
    spots: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        # Phase 6 #49: {"active": [{"prefix_hash", "count", "is_blind_spot", ...}]}
        if "active" in payload and isinstance(payload["active"], list):
            spots = [
                {
                    "prefix_hash": item["prefix_hash"],
                    "count": item["count"],
                    "samples": [item.get("sample_excerpt", "")],
                }
                for item in payload["active"]
                if item.get("is_blind_spot")
            ]
        else:
            spots = payload.get("spots") or payload.get("blind_spots") or []
            if not spots and payload:
                # Plain {prefix: count} map
                spots = [
                    {"prefix_hash": k, "count": v if isinstance(v, int) else int(v.get("count", 0)), "samples": v.get("samples", []) if isinstance(v, dict) else []}
                    for k, v in payload.items()
                ]
    elif isinstance(payload, list):
        spots = payload
    return [s for s in spots if isinstance(s, dict) and int(s.get("count", 0)) >= MIN_SAMPLE_THRESHOLD]


async def autodraft_for_blind_spots(
    blind_spots: list[dict[str, Any]] | None = None,
    *,
    url: str = "http://127.0.0.1:47820/wiki/v1/blind_spots",
    out_dir: Path | None = None,
    max_drafts: int = MAX_DRAFTS_PER_RUN,
) -> list[Path]:
    """Draft up to ``max_drafts`` markdown sources for the highest-frequency blind-spots."""
    if blind_spots is None:
        blind_spots = fetch_blind_spots(url)
    blind_spots = sorted(blind_spots, key=lambda s: int(s.get("count", 0)), reverse=True)[:max_drafts]

    if not blind_spots:
        logger.info("Active learning: nothing to draft (no blind-spots above threshold)")
        return []

    if not (config.MOCK_LLM or config.llm_configured()):
        raise RuntimeError(
            "Active learning needs DEEPSEEK_API_KEY (or ROSCLAW_KNOW_MOCK_LLM=1)"
        )

    written: list[Path] = []
    async with aiohttp.ClientSession() as session:
        tasks = [_draft_one(spot, session) for spot in blind_spots]
        for result in await asyncio.gather(*tasks):
            if result is None:
                continue
            prefix_hash, body = result
            path = _write_draft(prefix_hash, body, out_dir=out_dir)
            written.append(path)
            logger.info(
                "Autodrafted %s (from blind-spot %s)",
                path.name, prefix_hash,
            )
    logger.info("Active learning: drafted %d new sources", len(written))
    return written


__all__ = [
    "AUTO_DRAFT_DIR",
    "DRAFT_SYSTEM_PROMPT",
    "MIN_SAMPLE_THRESHOLD",
    "MAX_DRAFTS_PER_RUN",
    "autodraft_for_blind_spots",
    "fetch_blind_spots",
]
