"""Load and validate ``data/eval_tasks/*.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .types import EvalTask

REQUIRED_KEYS = {
    "task_id",
    "description",
    "entrypoint",
    "scoring_fn_name",
    "objective_direction",
    "metric_name",
    "max_iters",
}
OPTIONAL_HINT_KEYS = ["canonical_hint", "placebo_hint", "shuffled_hint", "task_pack_hint"]


def _validate(raw: dict[str, Any], path: Path) -> None:
    missing = REQUIRED_KEYS - raw.keys()
    if missing:
        raise ValueError(f"{path}: missing required keys {sorted(missing)}")
    if raw.get("objective_direction") not in {"maximize", "minimize"}:
        raise ValueError(
            f"{path}: objective_direction must be 'maximize' or 'minimize', "
            f"got {raw.get('objective_direction')!r}"
        )


def load_task(path: Path) -> EvalTask:
    """Load a single task YAML file."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    _validate(raw, path)
    hints = {k: raw.get(k, "") for k in OPTIONAL_HINT_KEYS}
    return EvalTask(
        task_id=raw["task_id"],
        description=raw["description"],
        entrypoint=raw["entrypoint"],
        scoring_fn_name=raw["scoring_fn_name"],
        objective_direction=raw["objective_direction"],
        metric_name=raw["metric_name"],
        max_iters=int(raw["max_iters"]),
        params=raw.get("params", {}),
        **hints,
    )


def load_tasks(glob_pattern: str) -> list[EvalTask]:
    """Load all task YAML files matching ``glob_pattern``.

    Files are sorted by filename for deterministic ordering.
    """
    paths = sorted(Path().glob(glob_pattern))
    if not paths:
        raise FileNotFoundError(f"no task YAML files matched {glob_pattern!r}")
    return [load_task(p) for p in paths]
