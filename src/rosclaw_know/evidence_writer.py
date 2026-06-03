"""Sprint 6: helpers for writing :class:`EvidenceTrace` records.

The runtime side (rosclaw-how) produces EvidenceTraces every time it
fires a CATALYST injection; this module gives it the three primitives it
needs to do that safely:

* :class:`EvidenceTraceWriter` — atomic append-only JSONL writer with a
  fsync barrier (so a crash mid-write doesn't truncate the file mid-
  record).
* :func:`compute_code_diff_hash` — sha256 of a *normalised* before/after
  pair, so we can de-dup near-identical diffs across runs without being
  fooled by comment-only or whitespace-only edits.
* :func:`detect_hint_use` — given a :attr:`code_diff_summary` (the list
  of human-readable diff phrases) and a list of hint_features (regex
  patterns from :mod:`hint_features`), return ``(used_hint, matched)``.

The module is import-light on purpose: it's expected to be called from
rosclaw-how's tight feedback path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .schemas import EvidenceTrace

log = logging.getLogger("rosclaw_know.evidence_writer")


# ── code-diff hash ───────────────────────────────────────────────────────


_LINE_COMMENT_RE = re.compile(r"^\s*#.*$", re.MULTILINE)
_BLANK_LINE_RE = re.compile(r"^\s*$\n", re.MULTILINE)
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def _normalise_source(src: str) -> str:
    """Whitespace / comment stripping used by :func:`compute_code_diff_hash`.

    Order matters — strip comments first, then trailing whitespace,
    then collapse blank lines, finally strip the trailing newline.
    After this two diffs that differ only in cosmetic edits hash the
    same.
    """
    out = _LINE_COMMENT_RE.sub("", src)
    out = _TRAILING_WS_RE.sub("", out)
    out = _BLANK_LINE_RE.sub("", out)
    return out.strip("\n")


def compute_code_diff_hash(before_src: str, after_src: str) -> str:
    """sha256 over the normalised before+after source pair.

    Returns a hex digest with a ``sha256:`` prefix so consumers can
    tell at a glance which family of hash this is.  Inputs are
    normalised by :func:`_normalise_source` so comment-only edits
    don't change the hash.
    """
    h = hashlib.sha256()
    h.update(_normalise_source(before_src).encode("utf-8"))
    h.update(b"\x00---DIFF---\x00")
    h.update(_normalise_source(after_src).encode("utf-8"))
    return f"sha256:{h.hexdigest()}"


# ── hint-use detection ──────────────────────────────────────────────────


def detect_hint_use(
    code_diff_summary: Iterable[str],
    hint_features: Iterable[str],
) -> tuple[bool, list[str]]:
    """Return ``(used_hint, matched_features)``.

    ``hint_features`` are regex patterns (per the
    :mod:`hint_features.yaml` registry).  We scan every line of
    ``code_diff_summary`` against every feature; the function is OR
    over both axes (any feature matching any line counts as used).

    Matches are case-insensitive — diff summaries are paraphrased prose
    so case carries no information.
    """
    diff_text = "\n".join(code_diff_summary).lower()
    matched: list[str] = []
    for raw in hint_features:
        try:
            pat = re.compile(raw, re.IGNORECASE)
        except re.error as exc:
            log.warning("bad hint_feature pattern %r: %s", raw, exc)
            continue
        if pat.search(diff_text):
            matched.append(raw)
    return bool(matched), matched


# ── JSONL writer ────────────────────────────────────────────────────────


class EvidenceTraceWriter:
    """Append-only JSONL writer for EvidenceTrace records.

    Designed to be cheap to instantiate and safe under concurrent
    writers in the same process.  Cross-process safety is **not** the
    goal — rosclaw-how runs one writer per worker, and we rotate file
    names by date (``evidence_traces_YYYYMMDD.jsonl``).

    Usage::

        writer = EvidenceTraceWriter(Path("data/exports/evidence_traces.jsonl"))
        writer.append(trace)
        ...
        writer.close()

    or as a context manager::

        with EvidenceTraceWriter(path) as w:
            w.append(trace)
    """

    def __init__(self, path: Path, *, autoflush: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Open in line-buffered mode so the OS buffer flushes per newline.
        self._fh = self.path.open("a", encoding="utf-8", buffering=1)
        self._autoflush = autoflush
        self._record_count = 0

    # ── core ──

    def append(self, trace: EvidenceTrace) -> None:
        """Append one EvidenceTrace.  Validates first (raises on bad input)."""
        # Re-validate to dodge tampering by the caller — strict by design.
        record = trace.model_dump(mode="json")
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._fh.write(line + "\n")
            if self._autoflush:
                self._fh.flush()
                try:
                    os.fsync(self._fh.fileno())
                except (OSError, ValueError):
                    # ``ValueError`` happens when fsync is called on a
                    # filesystem that doesn't support it (e.g. tmpfs in
                    # some CI containers).  Don't crash the runtime.
                    pass
            self._record_count += 1

    def append_many(self, traces: Iterable[EvidenceTrace]) -> int:
        """Bulk append; returns count written."""
        n = 0
        for t in traces:
            self.append(t)
            n += 1
        return n

    # ── lifecycle ──

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
                self._fh.close()
            except OSError as exc:
                log.warning("error closing %s: %s", self.path, exc)

    def __enter__(self) -> EvidenceTraceWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── introspection ──

    @property
    def record_count(self) -> int:
        return self._record_count


# ── streaming reader (used by evidence_distill + tests) ─────────────────


def stream_traces(path: Path) -> Iterator[EvidenceTrace]:
    """Stream JSONL → validated EvidenceTrace.  Logs and skips bad lines."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                yield EvidenceTrace.model_validate(obj)
            except (json.JSONDecodeError, Exception) as exc:
                log.warning(
                    "%s:%d skipping malformed trace: %s",
                    path.name, lineno, exc,
                )


@contextmanager
def temp_writer(path: Path) -> Iterator[EvidenceTraceWriter]:
    """Tiny context manager for tests + scripts."""
    w = EvidenceTraceWriter(path)
    try:
        yield w
    finally:
        w.close()


__all__ = [
    "EvidenceTraceWriter",
    "compute_code_diff_hash",
    "detect_hint_use",
    "stream_traces",
    "temp_writer",
]
