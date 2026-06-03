"""Feedback distillation — turn rosclaw-how injection_outcomes into pattern metrics.

The companion module for Phase 4's closed feedback loop. rosclaw-how writes
its ``injection_outcomes`` SeekDB collection to JSONL files in
``data/exports/outcomes-YYYYMMDD.jsonl``; this module reads them and produces
``data/assets/pattern_metrics.json`` keyed by pattern_id.

The output drives :mod:`rosclaw_know.curated_publisher`'s reweight pass — any
cluster whose ``uplift_mean`` is consistently negative (and has enough
samples) gets demoted via a priority field that the runtime respects.

Format expected per outcome (one JSON object per line):

    {"injection_id": "uuid", "symptom": "...", "pattern_id": "anti_windup_pid",
     "similarity": 0.71, "pre_score": 0.45, "post_score": 0.62,
     "delta_score": 0.17, "iterations_to_resolve": 3,
     "agent_notes": null, "ts": "2026-05-18T..."}

Unknown fields are tolerated; missing required fields skip the record with a
WARNING. We do not load the whole file into memory — outcomes are streamed.
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ASSETS_DIR, PROJECT_ROOT

logger = logging.getLogger("rosclaw_know.feedback_distill")

# Threshold for "win" — same magnitude as adaptive_state_router's plateau test.
WIN_DELTA_THRESHOLD = 0.05

# Patterns with fewer than this many samples are not considered for soft
# deprecation; small-sample noise must not promote or demote anything.
MIN_SAMPLE_SIZE = 5

DEFAULT_EXPORTS_DIR = PROJECT_ROOT.parent / "rosclaw-how" / "data" / "exports"


@dataclass(frozen=True)
class PatternMetric:
    """Per-pattern aggregate statistics over all observed outcomes."""

    pattern_id: str
    n: int
    uplift_mean: float
    uplift_std: float
    win_rate: float
    last_seen: str  # ISO8601


def _iter_outcome_files(exports_dir: Path) -> list[Path]:
    """Find all outcomes-*.jsonl files under the export directory.

    Sorted lexicographically so date-stamped files yield deterministic order.
    """
    if not exports_dir.exists():
        logger.warning("Exports dir not found at %s", exports_dir)
        return []
    return sorted(p for p in exports_dir.glob("outcomes-*.jsonl") if p.is_file())


def _stream_outcomes(paths: Iterable[Path]) -> Iterator[dict]:
    """Yield outcomes from many jsonl files. Bad lines are logged and skipped."""
    for fp in paths:
        with fp.open(encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning("%s:%d malformed JSON: %s", fp.name, lineno, exc)


def _required(rec: dict, fields: tuple[str, ...]) -> bool:
    """True iff record has every required field with a non-None value."""
    return all(rec.get(f) is not None for f in fields)


REQUIRED = ("pattern_id", "delta_score", "ts")


def aggregate(outcomes: Iterable[dict]) -> dict[str, PatternMetric]:
    """Aggregate outcomes into per-pattern metrics.

    ``win_rate`` is the fraction of outcomes where ``delta_score`` exceeds
    :data:`WIN_DELTA_THRESHOLD`. ``uplift_std`` uses sample stdev (n-1); for
    n < 2 it is reported as 0.0 to avoid a ``StatisticsError`` propagating.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for o in outcomes:
        if not _required(o, REQUIRED):
            continue
        buckets[str(o["pattern_id"])].append(o)

    metrics: dict[str, PatternMetric] = {}
    for pid, recs in buckets.items():
        deltas = [float(r["delta_score"]) for r in recs]
        n = len(deltas)
        mean = statistics.fmean(deltas)
        std = statistics.stdev(deltas) if n > 1 else 0.0
        wins = sum(1 for d in deltas if d > WIN_DELTA_THRESHOLD)
        last_seen = max(str(r["ts"]) for r in recs)
        metrics[pid] = PatternMetric(
            pattern_id=pid,
            n=n,
            uplift_mean=round(mean, 4),
            uplift_std=round(std, 4),
            win_rate=round(wins / n, 4),
            last_seen=last_seen,
        )
    return metrics


def write_metrics(metrics: dict[str, PatternMetric], out_path: Path | None = None) -> Path:
    """Serialize metrics to ``data/assets/pattern_metrics.json``."""
    if out_path is None:
        out_path = ASSETS_DIR / "pattern_metrics.json"
    payload = {
        "schema_version": 1,
        "win_delta_threshold": WIN_DELTA_THRESHOLD,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "patterns": {pid: asdict(m) for pid, m in sorted(metrics.items())},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def distill(exports_dir: Path | None = None, out_path: Path | None = None) -> dict[str, PatternMetric]:
    """End-to-end: read exports, aggregate, write metrics. Returns the metrics dict."""
    exports_dir = exports_dir or DEFAULT_EXPORTS_DIR
    paths = _iter_outcome_files(exports_dir)
    if not paths:
        logger.info("No outcome export files in %s — writing empty metrics", exports_dir)
        metrics: dict[str, PatternMetric] = {}
    else:
        logger.info("Reading %d outcome export file(s) from %s", len(paths), exports_dir)
        metrics = aggregate(_stream_outcomes(paths))

    written = write_metrics(metrics, out_path)
    logger.info(
        "Distilled %d patterns from %d export file(s) → %s",
        len(metrics), len(paths), written,
    )
    return metrics


def is_demoted(metric: PatternMetric, *, threshold: float = -0.05) -> bool:
    """True if a pattern has enough negative signal to warrant soft deprecation."""
    if metric.n < MIN_SAMPLE_SIZE:
        return False
    return metric.uplift_mean < threshold


__all__ = [
    "PatternMetric",
    "WIN_DELTA_THRESHOLD",
    "MIN_SAMPLE_SIZE",
    "DEFAULT_EXPORTS_DIR",
    "aggregate",
    "write_metrics",
    "distill",
    "is_demoted",
]
