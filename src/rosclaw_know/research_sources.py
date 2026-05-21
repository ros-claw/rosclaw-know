"""Multi-source research fetcher.

When an agent asks ``rosclaw-know`` to research a topic, this module
resolves the topic into concrete markdown source files the existing
harvester can process. Three source channels (best-effort, any may
return zero results without failing the run):

  * **arXiv** — search API → top-N paper abstracts + URLs.
  * **GitHub** — search repos / READMEs by topic keyword.
  * **Web** — Brave / Tavily (only if API key set in env), falling back
    to nothing rather than scraping random sites.

Each fetched source is wrapped in a ``FetchedSource`` dataclass with a
``source_type`` tag (``paper`` / ``repo`` / ``web``) the harvester can
use to pick the right extractor prompt later.

This module is deliberately small and ROBUST to network failures —
research jobs should degrade gracefully when (say) arXiv is rate-limited.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

logger = logging.getLogger("rosclaw_know.research_sources")

# Per-source caps so a single research job doesn't dominate the LLM budget
_ARXIV_RESULTS = 8
_GITHUB_RESULTS = 6
_WEB_RESULTS = 6

_USER_AGENT = (
    "rosclaw-know-research/0.9 (+https://github.com/ros-claw/rosclaw-know)"
)
_TIMEOUT = 10


@dataclass(frozen=True)
class FetchedSource:
    """A single research source ready to be written to disk."""

    source_type: str  # "paper" | "repo" | "web"
    title: str
    url: str
    markdown_body: str
    filename: str  # safe-slugified, ends in .md


# ── arXiv ────────────────────────────────────────────────────────────────


_ARXIV_API = "http://export.arxiv.org/api/query"


def _arxiv_search(topic: str, max_results: int = _ARXIV_RESULTS) -> list[FetchedSource]:
    qs = urllib.parse.urlencode({
        "search_query": f"all:{topic}",
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{_ARXIV_API}?{qs}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}),
            timeout=_TIMEOUT,
        ) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("arXiv search failed: %s", exc)
        return []

    out: list[FetchedSource] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("arXiv XML malformed: %s", exc)
        return []

    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        link_el = entry.find("a:id", ns)
        url_paper = link_el.text.strip() if link_el is not None and link_el.text else ""
        if not (title and summary and url_paper):
            continue
        body = (
            f"# {title}\n\n"
            f"**Source**: arXiv paper · {url_paper}\n\n"
            f"## Abstract\n\n{summary}\n"
        )
        out.append(FetchedSource(
            source_type="paper",
            title=title,
            url=url_paper,
            markdown_body=body,
            filename=_slug(title) + ".md",
        ))
    return out


# ── GitHub ──────────────────────────────────────────────────────────────


_GITHUB_API = "https://api.github.com"


def _github_search(topic: str, max_results: int = _GITHUB_RESULTS) -> list[FetchedSource]:
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Bias toward awesome lists + tutorials when a topic search is broad.
    qs = urllib.parse.urlencode({
        "q": f"{topic} in:readme topic:awesome OR topic:tutorial",
        "sort": "stars",
        "order": "desc",
        "per_page": max_results,
    })
    try:
        req = urllib.request.Request(f"{_GITHUB_API}/search/repositories?{qs}", headers=headers)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.warning("GitHub search failed: %s", exc)
        return []

    out: list[FetchedSource] = []
    for item in data.get("items", [])[:max_results]:
        full_name = item.get("full_name", "")
        description = item.get("description", "") or ""
        if not full_name:
            continue
        readme = _github_readme(full_name, headers)
        if not readme:
            continue
        title = item.get("name", full_name)
        body = (
            f"# {title}\n\n"
            f"**Source**: GitHub repo `{full_name}` · "
            f"https://github.com/{full_name}\n\n"
            f"_{description}_\n\n## README\n\n{readme}\n"
        )
        out.append(FetchedSource(
            source_type="repo",
            title=title,
            url=f"https://github.com/{full_name}",
            markdown_body=body,
            filename=_slug(full_name.replace("/", "_")) + ".md",
        ))
    return out


def _github_readme(full_name: str, headers: dict[str, str]) -> str | None:
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{full_name}/{branch}/README.md"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}),
                timeout=_TIMEOUT,
            ) as resp:
                text = resp.read(80_000).decode("utf-8", errors="replace")
                if len(text) >= 200:
                    return text
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            continue
    return None


# ── Web search (Brave) ──────────────────────────────────────────────────


def _brave_search(topic: str, max_results: int = _WEB_RESULTS) -> list[FetchedSource]:
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if not key:
        return []
    try:
        req = urllib.request.Request(
            "https://api.search.brave.com/res/v1/web/search?"
            + urllib.parse.urlencode({"q": topic, "count": max_results}),
            headers={
                "X-Subscription-Token": key,
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.warning("Brave search failed: %s", exc)
        return []

    out: list[FetchedSource] = []
    for r in (payload.get("web", {}).get("results") or [])[:max_results]:
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("description", "") or ""
        if not (title and url):
            continue
        body = (
            f"# {title}\n\n"
            f"**Source**: web · {url}\n\n"
            f"## Snippet\n\n{snippet}\n"
        )
        out.append(FetchedSource(
            source_type="web",
            title=title,
            url=url,
            markdown_body=body,
            filename=_slug(title) + ".md",
        ))
    return out


# ── slug / orchestration ────────────────────────────────────────────────


def _slug(text: str, *, max_chars: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-]+", "_", text).strip("_").lower()
    return (s[:max_chars] or hashlib.sha1(text.encode("utf-8")).hexdigest()[:12])


async def collect_sources(
    topic: str, *, depth: str = "shallow", budget_tokens: int = 50_000
) -> list[FetchedSource]:
    """Resolve a topic into a list of fetched markdown sources.

    Runs the three blocking channels in parallel via run_in_executor.
    """
    loop = asyncio.get_running_loop()
    max_per_source = _ARXIV_RESULTS if depth == "shallow" else _ARXIV_RESULTS * 2

    arxiv, github, web = await asyncio.gather(
        loop.run_in_executor(None, _arxiv_search, topic, max_per_source),
        loop.run_in_executor(None, _github_search, topic, _GITHUB_RESULTS),
        loop.run_in_executor(None, _brave_search, topic, _WEB_RESULTS),
    )

    combined = arxiv + github + web
    # De-duplicate by URL (some topics produce same arXiv via web hit)
    seen: set[str] = set()
    unique: list[FetchedSource] = []
    for s in combined:
        if s.url in seen:
            continue
        seen.add(s.url)
        unique.append(s)

    logger.info(
        "Topic %r → arxiv=%d, github=%d, web=%d, unique=%d, depth=%s",
        topic, len(arxiv), len(github), len(web), len(unique), depth,
    )
    return unique


__all__ = [
    "FetchedSource",
    "collect_sources",
]
