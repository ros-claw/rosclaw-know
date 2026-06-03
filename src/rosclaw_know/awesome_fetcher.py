"""Awesome-list fetcher — pull curated link collections into wiki/auto_drafted/.

Phase 8 takes the active-learning loop one step further: instead of letting
DeepSeek hallucinate fixes for cold-spots, we seed the knowledge base with
real, curated material from public awesome lists such as
``hslatman/awesome-industrial-control-system-security`` and
``A-make/awesome-control-theory``.

Pipeline
--------

1. Fetch the awesome list's README from GitHub (raw URL).
2. Parse the markdown into entries of shape:
       ``{title, url, description, section}``
3. For each entry, download the referenced content with type-aware
   strategies:
       * GitHub repo URL → raw README.md
       * Blog / article (HTML) → text-strip the body
       * PDF → metadata only (we cannot OCR here)
       * Other → skip with reason
4. Write each entry as a markdown file under
   ``<out_dir>/<list_slug>/<entry_slug>.md`` with frontmatter linking
   back to source. The harvester picks them up like any other source.

This module deliberately avoids real LLM calls — fetch is plain HTTP. The
heavy lifting (extraction, weaver, Muse) happens downstream in
``scripts/ingest.py``.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import WIKI_DIR

logger = logging.getLogger("rosclaw_know.awesome_fetcher")

# How much body content to keep per entry — keeps the corpus disk-bounded
# and downstream LLM token use predictable.
MAX_ENTRY_BYTES = 80_000

# Per-fetch timeout. Awesome lists frequently link to dead URLs, so we want
# to fail fast rather than block the whole batch.
HTTP_TIMEOUT = 10

# Friendly UA so GitHub doesn't 403 us.
USER_AGENT = (
    "rosclaw-know-awesome-fetcher/0.1 (+https://github.com/ruvnet/claude-flow)"
)

DEFAULT_OUT_DIR = WIKI_DIR / "awesome_corpus"


@dataclass(frozen=True)
class AwesomeEntry:
    """One bullet-point entry parsed from an awesome list README."""

    title: str
    url: str
    description: str
    section: str  # Markdown heading above this entry ("## Network monitoring")


@dataclass
class FetchResult:
    """Outcome of trying to download one entry's referenced content."""

    entry: AwesomeEntry
    path: Path | None = None
    skipped_reason: str | None = None
    bytes_written: int = 0
    fetch_kind: str = ""  # "github_readme" | "html_text" | "pdf_meta" | "skip"


# ── Markdown parsing ─────────────────────────────────────────────────────

# Awesome-list bullets look like:
#   - [Title](https://...) - Optional description.
#   * [Title](https://...) - Optional description.
# Some lists use `–`, `—`, or `:` instead of `-` between url and description;
# many also just put the description right after a single space.
_BULLET_PAT = re.compile(
    r"^\s*[-*]\s+\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)"
    r"(?:\s*[-–—:]?\s*(?P<desc>.+?))?\s*$",
    re.MULTILINE,
)

_HEADING_PAT = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)

# Fallback: many awesome lists (e.g. hslatman/awesome-industrial-control-system-security)
# arrange entries in HTML tables rather than markdown bullets. Each row contains
# a <td> with an <a href> and another <td> with the description.
_HTML_LINK_PAT = re.compile(
    r'<a\s+[^>]*href="(?P<url>https?://[^"]+)"[^>]*>(?P<title>[^<]+)</a>',
    re.IGNORECASE,
)


def parse_readme(markdown: str) -> list[AwesomeEntry]:
    """Walk the markdown linearly: remember the most recent heading, yield
    every bullet (with absolute http(s) URLs) with the current heading as
    section. Anchor links, relative paths, and ``mailto:`` are filtered.
    """
    out: list[AwesomeEntry] = []
    current_section = "(top)"
    # Index headings by their start offset so we can binary-search per bullet.
    headings: list[tuple[int, str]] = [
        (m.start(), m.group("title").strip()) for m in _HEADING_PAT.finditer(markdown)
    ]
    # Pointer into headings list; advance as we iterate bullets.
    h_idx = 0
    for m in _BULLET_PAT.finditer(markdown):
        url = m.group("url").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue  # skip anchors (#...), relative paths, mailto:, etc.
        # Advance h_idx so headings[h_idx] is the last heading before m.
        while h_idx + 1 < len(headings) and headings[h_idx + 1][0] < m.start():
            h_idx += 1
        if headings and headings[h_idx][0] < m.start():
            current_section = headings[h_idx][1]
        out.append(
            AwesomeEntry(
                title=m.group("title").strip(),
                url=url,
                description=(m.group("desc") or "").strip(),
                section=current_section,
            )
        )

    # Fallback: parse HTML <a href> tags for lists arranged as tables.
    # We only fall through to HTML when markdown bullet parsing yielded
    # next to nothing — otherwise we'd double-count entries.
    if len(out) < 5 and "<a " in markdown.lower():
        out = _parse_html_anchors(markdown, headings)
    return out


def _parse_html_anchors(
    markdown: str, headings: list[tuple[int, str]]
) -> list[AwesomeEntry]:
    """Pull every absolute-http <a href> from the document.

    Tracks the most-recent ``##`` heading as the section. Skips anchors
    pointing at the same repo's CONTRIBUTING.md / LICENSE / similar
    boilerplate.
    """
    seen_urls: set[str] = set()
    out: list[AwesomeEntry] = []
    current_section = "(top)"
    h_idx = 0
    for m in _HTML_LINK_PAT.finditer(markdown):
        url = m.group("url").strip()
        if url in seen_urls:
            continue
        # Skip obvious boilerplate / contribution links.
        if any(skip in url.lower() for skip in (
            "/contributing", "/license", "/code_of_conduct",
            "/issues", "/pulls", "/blob/master/readme",
        )):
            continue
        title = m.group("title").strip()
        if len(title) < 2:
            continue
        while h_idx + 1 < len(headings) and headings[h_idx + 1][0] < m.start():
            h_idx += 1
        if headings and headings[h_idx][0] < m.start():
            current_section = headings[h_idx][1]
        seen_urls.add(url)
        out.append(
            AwesomeEntry(
                title=title,
                url=url,
                description="",  # HTML tables put the desc in a sibling cell — too noisy to chase
                section=current_section,
            )
        )
    return out


# ── HTTP helpers ─────────────────────────────────────────────────────────


def _fetch_bytes(url: str, *, timeout: int = HTTP_TIMEOUT, max_bytes: int = MAX_ENTRY_BYTES) -> bytes | None:
    """GET ``url`` and return up to ``max_bytes``; return None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(max_bytes)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        logger.debug("HTTP fetch failed for %s: %s", url, exc)
        return None
    except (TimeoutError, ConnectionError) as exc:
        logger.debug("Network error fetching %s: %s", url, exc)
        return None


def fetch_awesome_readme(repo_url: str) -> str | None:
    """Resolve a GitHub repo URL to its raw README and return as text.

    Tries both ``master`` and ``main`` branches; some lists are on either.
    """
    m = re.match(r"^https?://github\.com/([^/]+)/([^/?#]+)(?:/.*)?$", repo_url.strip())
    if not m:
        logger.warning("Not a GitHub repo URL: %s", repo_url)
        return None
    owner, repo = m.group(1), m.group(2)
    for branch in ("master", "main"):
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        data = _fetch_bytes(raw_url, max_bytes=512_000)
        if data:
            return data.decode("utf-8", errors="replace")
    logger.warning("No README found on master or main for %s/%s", owner, repo)
    return None


# ── Per-entry download strategies ────────────────────────────────────────


def _classify(url: str) -> str:
    u = url.lower()
    if "github.com/" in u and not u.endswith((".pdf", ".zip", ".tar.gz")):
        return "github_repo"
    if u.endswith(".pdf") or "/pdf/" in u or "arxiv.org/pdf" in u:
        return "pdf"
    return "html"


def _download_github(url: str) -> tuple[str, str] | None:
    text = fetch_awesome_readme(url)
    return ("github_readme", text) if text else None


_HTML_TAGS = re.compile(r"<[^>]+>")
_HTML_WS = re.compile(r"\n{3,}")


def _download_html(url: str) -> tuple[str, str] | None:
    data = _fetch_bytes(url, max_bytes=MAX_ENTRY_BYTES)
    if not data:
        return None
    text = data.decode("utf-8", errors="replace")
    # Best-effort body extraction: drop scripts/styles, then strip tags.
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = _HTML_TAGS.sub(" ", text)
    text = _HTML_WS.sub("\n\n", text).strip()
    return ("html_text", text)


def _download_pdf(url: str) -> tuple[str, str] | None:
    # We don't OCR — we just record the metadata so a future enrichment pass
    # can pick it up. The downstream harvester will skip it as too short, by
    # design.
    return ("pdf_meta", f"# PDF reference\n\nURL: {url}\n\n_This entry references a PDF; no body extracted._\n")


# ── Top-level orchestration ──────────────────────────────────────────────


def _slug(text: str, *, max_chars: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\- ]+", "", text).strip().lower()
    s = re.sub(r"\s+", "_", s)
    return s[:max_chars] or "untitled"


def _write_entry_md(
    entry: AwesomeEntry,
    fetch_kind: str,
    body: str,
    list_slug: str,
    out_dir: Path,
) -> Path:
    entry_slug = _slug(entry.title)
    out_dir = out_dir / list_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"{entry_slug}.md"
    frontmatter = (
        "---\n"
        f"source: {entry.url}\n"
        f"title: {entry.title}\n"
        f"section: {entry.section}\n"
        f"awesome_list: {list_slug}\n"
        f"fetched_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"fetch_kind: {fetch_kind}\n"
        f"phase: 8-awesome-ingest\n"
        "priority: 0   # staging — review before promotion\n"
        "---\n\n"
    )
    header = f"# {entry.title}\n\n"
    if entry.description:
        header += f"_{entry.description}_\n\n"
    fp.write_text(frontmatter + header + body, encoding="utf-8")
    return fp


def fetch_one(
    entry: AwesomeEntry, list_slug: str, out_dir: Path,
) -> FetchResult:
    """Best-effort download + write for a single entry."""
    kind = _classify(entry.url)
    strategy: dict[str, callable] = {  # type: ignore[type-arg]
        "github_repo": _download_github,
        "html": _download_html,
        "pdf": _download_pdf,
    }
    fn = strategy.get(kind)
    if fn is None:
        return FetchResult(entry=entry, skipped_reason=f"unsupported url kind={kind}")
    pair = fn(entry.url)
    if pair is None:
        return FetchResult(entry=entry, skipped_reason=f"fetch failed ({kind})")
    fetch_kind, body = pair
    if len(body) < 200 and fetch_kind != "pdf_meta":
        return FetchResult(entry=entry, skipped_reason=f"body too short ({len(body)}b)")
    fp = _write_entry_md(entry, fetch_kind, body, list_slug, out_dir)
    return FetchResult(entry=entry, path=fp, bytes_written=len(body), fetch_kind=fetch_kind)


def fetch_awesome_list(
    list_url: str,
    *,
    list_slug: str | None = None,
    out_dir: Path | None = None,
    limit: int | None = None,
    per_fetch_sleep: float = 0.4,
    sections_filter: Iterable[str] | None = None,
) -> list[FetchResult]:
    """End-to-end: fetch README, parse, download each entry, write to disk.

    ``limit`` caps the number of entries actually downloaded (useful for a
    smoke run). ``sections_filter`` (case-insensitive substring match)
    restricts to sections like "papers" or "tools".
    """
    out_dir = out_dir or DEFAULT_OUT_DIR
    readme = fetch_awesome_readme(list_url)
    if readme is None:
        logger.error("Could not fetch awesome list README from %s", list_url)
        return []

    if list_slug is None:
        m = re.search(r"github\.com/[^/]+/([^/?#]+)", list_url)
        list_slug = _slug(m.group(1)) if m else "unknown_list"

    entries = parse_readme(readme)
    logger.info("Parsed %d entries from %s", len(entries), list_url)

    if sections_filter:
        wanted = tuple(s.lower() for s in sections_filter)
        entries = [e for e in entries if any(w in e.section.lower() for w in wanted)]
        logger.info("After section filter %s: %d entries", list(wanted), len(entries))

    if limit is not None:
        entries = entries[:limit]

    results: list[FetchResult] = []
    for i, e in enumerate(entries):
        if per_fetch_sleep and i:
            time.sleep(per_fetch_sleep)
        results.append(fetch_one(e, list_slug, out_dir))
        if (i + 1) % 10 == 0:
            ok = sum(1 for r in results if r.path is not None)
            logger.info("  ... %d/%d fetched, %d successful", i + 1, len(entries), ok)
    ok = sum(1 for r in results if r.path is not None)
    logger.info(
        "Awesome list %s: %d/%d entries successfully fetched → %s",
        list_slug, ok, len(entries), out_dir / list_slug,
    )
    return results


__all__ = [
    "AwesomeEntry",
    "DEFAULT_OUT_DIR",
    "FetchResult",
    "MAX_ENTRY_BYTES",
    "fetch_awesome_list",
    "fetch_awesome_readme",
    "fetch_one",
    "parse_readme",
]
