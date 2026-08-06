"""Explicit KnowStore construction; no silent production fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import KnowStore, StoreConfigurationError
from .memory import InMemoryKnowStore
from .seekdb import SeekDBKnowStore


def create_know_store(
    *, mode: str, allow_test_memory: bool = False, path: str | Path | None = None, **kwargs: Any
) -> KnowStore:
    if mode == "memory":
        if not allow_test_memory:
            raise StoreConfigurationError(
                "memory KnowStore is test-only; set allow_test_memory=True explicitly"
            )
        return InMemoryKnowStore()
    if mode in {"embedded", "server"}:
        return SeekDBKnowStore(mode=mode, path=path, **kwargs)
    raise StoreConfigurationError(f"unknown KnowStore mode: {mode!r}")
