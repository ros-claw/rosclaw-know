"""Transactional server-SQL migration runner for Know v2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_sql: str
    down_sql: str


def load_migrations(root: str | Path) -> list[Migration]:
    path = Path(root)
    migrations: list[Migration] = []
    for up in sorted(path.glob("*_up.sql")):
        prefix, name = up.stem.removesuffix("_up").split("_", 1)
        down = up.with_name(f"{prefix}_{name}_down.sql")
        if not down.is_file():
            raise ValueError(f"missing rollback migration: {down}")
        migrations.append(
            Migration(
                version=int(prefix),
                name=name,
                up_sql=up.read_text(encoding="utf-8"),
                down_sql=down.read_text(encoding="utf-8"),
            )
        )
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"migration versions must be contiguous from 1: {versions}")
    return migrations


def _statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


class SeekDBMigrationRunner:
    """Apply/rollback migrations against a PEP-249 SeekDB connection."""

    def __init__(self, connection: Any, migrations: list[Migration]) -> None:
        self.connection = connection
        self.migrations = migrations

    def _ensure_history(self, cursor: Any) -> None:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS know_schema_migration ("
            "version BIGINT PRIMARY KEY, name VARCHAR(255) NOT NULL, "
            "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    def current_version(self) -> int:
        with self.connection.cursor() as cursor:
            self._ensure_history(cursor)
            cursor.execute("SELECT COALESCE(MAX(version), 0) AS version FROM know_schema_migration")
            row = cursor.fetchone()
        if isinstance(row, dict):
            return int(row["version"])
        return int(row[0])

    def migrate(self, target: int | None = None) -> int:
        target = len(self.migrations) if target is None else target
        if target < 0 or target > len(self.migrations):
            raise ValueError(f"invalid migration target: {target}")
        current = self.current_version()
        if current == target:
            return current
        if current > target:
            return self.rollback(target)
        try:
            self.connection.begin()
            with self.connection.cursor() as cursor:
                for migration in self.migrations[current:target]:
                    for statement in _statements(migration.up_sql):
                        cursor.execute(statement)
                    cursor.execute(
                        "INSERT INTO know_schema_migration(version, name) VALUES (%s, %s)",
                        (migration.version, migration.name),
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return target

    def rollback(self, target: int) -> int:
        current = self.current_version()
        if target < 0 or target > current:
            raise ValueError(f"invalid rollback target: {target}")
        try:
            self.connection.begin()
            with self.connection.cursor() as cursor:
                for migration in reversed(self.migrations[target:current]):
                    for statement in _statements(migration.down_sql):
                        cursor.execute(statement)
                    cursor.execute(
                        "DELETE FROM know_schema_migration WHERE version = %s",
                        (migration.version,),
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return target
