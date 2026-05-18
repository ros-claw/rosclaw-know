"""Source manifest — track which wiki files have been processed.

Phase 5 ingest needs an idempotent way to ask: "is this file new, changed,
or already up-to-date?". Hashing every file on every run is the only
reliable detector: timestamps lie when files come over rsync / git and
length-only checks miss in-place edits.

The manifest lives at ``data/source_manifest.json``:

    {
      "schema_version": 1,
      "files": {
        "<absolute_path>": {
          "sha256": "...",
          "domain": "Control_Locomotion" | null,
          "first_processed": "2026-05-17T...",
          "last_processed": "2026-05-17T...",
          "n_clusters_contributed": 3
        },
        ...
      }
    }

Absolute paths are intentional — re-anchoring the wiki directory should
invalidate the manifest (we can't be sure the content corresponds to the
same logical document just because the relative path matches).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import DATA_DIR

logger = logging.getLogger("rosclaw_know.source_manifest")

DEFAULT_MANIFEST_PATH = DATA_DIR / "source_manifest.json"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SourceRecord:
    """A single tracked source file."""

    abs_path: str
    sha256: str
    domain: str | None
    first_processed: str
    last_processed: str
    n_clusters_contributed: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def sha256_of(path: Path) -> str:
    """Streaming SHA-256 of a file's bytes. 8 KiB chunks — RAM-friendly."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class SourceManifest:
    """In-memory view of source_manifest.json with disk-backed persistence."""

    path: Path = field(default_factory=lambda: DEFAULT_MANIFEST_PATH)
    files: dict[str, SourceRecord] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "SourceManifest":
        """Read manifest from disk, returning an empty one if absent or malformed."""
        path = path or DEFAULT_MANIFEST_PATH
        if not path.exists():
            return cls(path=path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s, starting fresh: %s", path, exc)
            return cls(path=path)
        files = {
            ap: SourceRecord(**entry)
            for ap, entry in payload.get("files", {}).items()
            if isinstance(entry, dict)
        }
        return cls(path=path, files=files)

    def save(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "files": {ap: asdict(rec) for ap, rec in self.files.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def upsert(
        self,
        file_path: Path,
        *,
        domain: str | None = None,
        n_clusters_contributed: int = 0,
    ) -> SourceRecord:
        """Insert or update a file record. Computes sha256 fresh."""
        ap = str(file_path.resolve())
        digest = sha256_of(file_path)
        now = _now_iso()
        existing = self.files.get(ap)
        rec = SourceRecord(
            abs_path=ap,
            sha256=digest,
            domain=domain if domain is not None else (existing.domain if existing else None),
            first_processed=existing.first_processed if existing else now,
            last_processed=now,
            n_clusters_contributed=n_clusters_contributed,
        )
        self.files[ap] = rec
        return rec

    def remove(self, file_path: Path) -> bool:
        """Drop a file from the manifest. Returns True if it was present."""
        ap = str(file_path.resolve())
        return self.files.pop(ap, None) is not None

    def status_of(self, file_path: Path) -> str:
        """Classify a file as ``"new"``, ``"changed"``, or ``"unchanged"``.

        ``"new"`` and ``"changed"`` mean the file needs (re)processing.
        Symlinked or missing paths are treated as ``"new"`` so the caller
        can decide whether to log and skip.
        """
        ap = str(file_path.resolve())
        if ap not in self.files:
            return "new"
        try:
            current_hash = sha256_of(file_path)
        except OSError:
            return "new"
        return "unchanged" if current_hash == self.files[ap].sha256 else "changed"

    def select_dirty(self, candidates: Iterable[Path]) -> list[tuple[Path, str]]:
        """Return [(path, status)] for files that are NEW or CHANGED only.

        Files whose manifest entry still matches their on-disk content are
        skipped — that's the whole point of the manifest.
        """
        out: list[tuple[Path, str]] = []
        for p in candidates:
            try:
                status = self.status_of(p)
            except OSError as exc:
                logger.debug("Skipping unreadable %s: %s", p, exc)
                continue
            if status in ("new", "changed"):
                out.append((p, status))
        return out

    def record_contribution(self, file_path: Path, *, n_extra_clusters: int) -> None:
        """Bump ``n_clusters_contributed`` for a file after a mining pass."""
        ap = str(file_path.resolve())
        if ap not in self.files:
            return
        prev = self.files[ap]
        self.files[ap] = replace(
            prev,
            n_clusters_contributed=prev.n_clusters_contributed + n_extra_clusters,
            last_processed=_now_iso(),
        )


__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "SCHEMA_VERSION",
    "SourceRecord",
    "SourceManifest",
    "sha256_of",
]
