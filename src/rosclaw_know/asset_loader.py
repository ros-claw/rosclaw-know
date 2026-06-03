"""Public asset loaders for runtime consumers.

The HTTP API used to hide this behind ``_try_load_task_pack_assets``.
Runtimes that embed rosclaw-know directly (e.g. ``rosclaw.know``) need
the same loader without spinning up FastAPI.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .schemas import FailureMode, PatternCardV2, TaskCard

logger = logging.getLogger("rosclaw_know.asset_loader")


def load_task_pack_assets(assets_dir: str | Path) -> dict[str, list[Any]] | None:
    """Load the task-pack catalogs from ``assets_dir``.

    Returns ``{"tasks": [...], "patterns": [...], "failures": [...]}`` on
    success, or ``None`` when any of the canonical YAMLs are missing.

    Caller decides what to do with ``None`` (HTTP 503, fall back to
    baseline, etc.).
    """
    assets_dir = Path(assets_dir)
    paths = {
        "tasks": assets_dir / "task_cards.yaml",
        "patterns": assets_dir / "pattern_cards_v2.yaml",
        "failures": assets_dir / "failure_taxonomy.yaml",
    }
    if not all(p.is_file() for p in paths.values()):
        missing = [str(p) for p in paths.values() if not p.is_file()]
        logger.warning("Task-pack assets incomplete; missing %s", missing)
        return None
    try:
        tasks_raw = yaml.safe_load(paths["tasks"].read_text(encoding="utf-8")) or {}
        patterns_raw = yaml.safe_load(paths["patterns"].read_text(encoding="utf-8")) or {}
        failures_raw = yaml.safe_load(paths["failures"].read_text(encoding="utf-8")) or {}
        return {
            "tasks": [TaskCard.model_validate(t) for t in tasks_raw.get("task_cards", [])],
            "patterns": [PatternCardV2.model_validate(p) for p in patterns_raw.get("pattern_cards", [])],
            "failures": [FailureMode.model_validate(f) for f in failures_raw.get("failures", [])],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load task-pack assets from %s: %s", assets_dir, exc)
        return None
