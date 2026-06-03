"""Sprint 9: Foxglove timeline annotation JSONL → :class:`RobotEvent`.

Foxglove Studio supports user-authored "annotations" / event markers
that get exported as JSONL.  The de-facto export shape is::

    {
      "id": "ann_001",
      "start_time": "2026-06-02T20:11:33.500Z",
      "end_time":   "2026-06-02T20:11:36.000Z",
      "label": "Collision with table",
      "category": "collision",
      "embodiment_id": "ur5",
      "metadata": {"object_a": "ur5/wrist", "object_b": "table"}
    }

We promote each annotation into one :class:`RobotEvent` so operator
walk-throughs become first-class evidence sources (operator-flagged
events are usually higher signal than auto-detected ones because they
involve a human's judgement call).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .event_schema import RobotEvent

log = logging.getLogger("rosclaw_know.sim_ingest.foxglove")

# Foxglove category → (RobotEvent.event_type, default severity)
_FOXGLOVE_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "collision":          ("collision",              "warning"),
    "estop":              ("safety_stop",            "safety_critical"),
    "e_stop":             ("safety_stop",            "safety_critical"),
    "safety_stop":        ("safety_stop",            "safety_critical"),
    "joint_limit":        ("joint_limit_violation",  "safety_critical"),
    "joint_violation":    ("joint_limit_violation",  "safety_critical"),
    "controller_error":   ("controller_error",       "warning"),
    "sensor_outlier":     ("sensor_outlier",         "warning"),
    "sensor_dropout":     ("sensor_outlier",         "warning"),
    "task_timeout":       ("task_timeout",           "warning"),
    "trajectory_error":   ("trajectory_deviation",   "warning"),
    "actuator_saturation": ("actuator_saturation",   "warning"),
}


def _ts_iso_to_ros(ts: Any) -> str:
    if isinstance(ts, (int, float)):
        return f"ros_time:{float(ts):.6f}"
    if isinstance(ts, str):
        # Foxglove ISO-8601; keep as-is — the downstream is opaque text.
        return ts
    return ""


def _fingerprint(category: str, payload: dict[str, Any], label: str) -> str:
    """Try to derive a structural fingerprint from metadata; fall back to label."""
    meta = payload.get("metadata") or payload.get("fields") or {}
    if not isinstance(meta, dict):
        meta = {}
    if category == "collision":
        a = str(meta.get("object_a", meta.get("body_a", "")))
        b = str(meta.get("object_b", meta.get("body_b", "")))
        return f"collision::{a}::{b}".rstrip(":")
    if category in ("joint_limit", "joint_violation"):
        return f"joint::{meta.get('joint', 'unknown')}"
    if category in ("estop", "e_stop", "safety_stop"):
        return "emergency_stop"
    if category == "controller_error":
        return f"controller::{meta.get('controller', 'unknown')}"
    if category in ("sensor_outlier", "sensor_dropout"):
        return f"sensor::{meta.get('sensor', 'unknown')}::{category}"
    # Fall back to a label-derived deterministic key.
    return f"foxglove::{label.strip().lower().replace(' ', '_')[:48]}"


def _iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    """Foxglove annotations may be exported as JSON-array or JSONL."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return
    if text.startswith("["):
        try:
            arr = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("foxglove array: failed to parse %s: %s", path, exc)
            return
        if isinstance(arr, list):
            for row in arr:
                if isinstance(row, dict):
                    yield row
        return
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("foxglove jsonl: skipping malformed line %d: %s", lineno, exc)
            continue
        if not isinstance(obj, dict):
            continue
        yield obj


def read_foxglove_jsonl(path: str | Path) -> list[RobotEvent]:
    """Parse Foxglove annotations → list of :class:`RobotEvent`."""
    p = Path(path)
    out: list[RobotEvent] = []
    for ann in _iter_rows(p):
        category = str(ann.get("category") or ann.get("type") or "").strip().lower()
        if category not in _FOXGLOVE_CATEGORY_MAP:
            continue
        event_type, default_sev = _FOXGLOVE_CATEGORY_MAP[category]
        sev = str(ann.get("severity") or default_sev)
        if sev not in ("info", "warning", "safety_critical"):
            sev = default_sev
        embodiment = str(ann.get("embodiment_id") or ann.get("robot") or "default_embodiment")
        ts = _ts_iso_to_ros(ann.get("start_time") or ann.get("timestamp"))
        label = str(ann.get("label") or ann.get("description") or "")
        fp = _fingerprint(category, ann, label)
        meta = ann.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"raw_metadata": meta}
        out.append(RobotEvent(
            timestamp=ts,
            event_type=event_type,  # type: ignore[arg-type]
            embodiment_id=embodiment,
            severity=sev,  # type: ignore[arg-type]
            fingerprint=fp,
            fields={"label": label, "annotation_id": ann.get("id"), **meta},
            source="foxglove",
            source_id=str(p),
        ))
    return out
