"""SQLite infrastructure for caching extracted heuristics + dedup state."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config


def _db_path(override: Path | None = None) -> Path:
    """Resolve DB path at call time so tests can rebind config.DB_PATH."""
    return override or config.DB_PATH


def init_db(db_path: Path | None = None) -> None:
    """Create the SQLite schema. Idempotent."""
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS heuristics (
                id              TEXT PRIMARY KEY,
                page_path       TEXT NOT NULL,
                symptom         TEXT NOT NULL,
                domain          TEXT NOT NULL,
                fix_pattern     TEXT,
                failed_attempt  TEXT,
                raw_content     TEXT,
                merged          INTEGER DEFAULT 0,
                extracted_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_heuristics_domain ON heuristics(domain);

            CREATE TABLE IF NOT EXISTS processed_files (
                file_md5     TEXT PRIMARY KEY,
                page_path    TEXT,
                outcome      TEXT,        -- 'extracted' | 'skipped_no_symptom' | 'skipped_dup' | 'llm_error'
                processed_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()


def open_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection. Caller closes."""
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def is_processed(conn: sqlite3.Connection, file_md5: str) -> bool:
    cur = conn.execute("SELECT 1 FROM processed_files WHERE file_md5 = ?", (file_md5,))
    return cur.fetchone() is not None


def mark_processed(
    conn: sqlite3.Connection,
    file_md5: str,
    page_path: str,
    outcome: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO processed_files (file_md5, page_path, outcome) VALUES (?, ?, ?)",
        (file_md5, page_path, outcome),
    )


def upsert_heuristic(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO heuristics
            (id, page_path, symptom, domain, fix_pattern, failed_attempt, raw_content, merged)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["page_path"],
            row["symptom"],
            row["domain"],
            row.get("fix_pattern") or "",
            row.get("failed_attempt") or "",
            row.get("raw_content") or "",
            1 if row.get("merged") else 0,
        ),
    )


def count_heuristics(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM heuristics")
    return cur.fetchone()[0]
