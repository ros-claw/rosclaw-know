"""Hard Know/Memory/Practice storage isolation guards."""

from __future__ import annotations

from pathlib import Path

from .base import StoreConfigurationError


def guard_store_isolation(
    *,
    know_database: str,
    memory_database: str | None = None,
    practice_database: str | None = None,
    know_path: str | Path | None = None,
    memory_path: str | Path | None = None,
    practice_path: str | Path | None = None,
) -> None:
    """Reject database-name and embedded-path aliasing across domains."""

    if not know_database.strip():
        raise StoreConfigurationError("Know database name must not be empty")
    for domain, database in (("Memory", memory_database), ("Practice", practice_database)):
        if database and database.strip().casefold() == know_database.strip().casefold():
            raise StoreConfigurationError(
                f"Know and {domain} must use separate databases: {know_database!r}"
            )

    if know_path is None:
        return
    know_resolved = Path(know_path).expanduser().resolve(strict=False)
    for domain, path in (("Memory", memory_path), ("Practice", practice_path)):
        if path is not None and Path(path).expanduser().resolve(strict=False) == know_resolved:
            raise StoreConfigurationError(
                f"Know and {domain} must use separate embedded paths: {know_resolved}"
            )
