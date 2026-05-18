"""Stats trajectory analysis — track pattern performance over time.

Phase 6 observability module. Polls rosclaw-how's ``/wiki/v1/stats``,
persists each snapshot under ``data/stats_history/``, and on demand
distills a per-pattern trajectory: improving / flat / degrading based on
the linear-regression slope of ``avg_uplift`` over the last ``N`` samples.

The output drives manual curation decisions:
  * "improving" patterns → safe to promote priority, may seed new ingest
  * "flat"      patterns → status quo
  * "degrading" patterns → candidate for ingest of fresh material or
                            soft-deprecation if already low absolute uplift

Single-shot CLI is intentional. A daemon would re-implement cron poorly;
cron / systemd / k8s-cronjob are better at scheduling than this module.
"""
from __future__ import annotations

import json
import logging
import statistics
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR

logger = logging.getLogger("rosclaw_know.stats_analyze")

STATS_HISTORY_DIR = DATA_DIR / "stats_history"
REPORTS_DIR = DATA_DIR / "reports"
DEFAULT_STATS_URL = "http://127.0.0.1:8088/wiki/v1/stats"

# Slopes thresholds — these are uplift_mean units per *snapshot*, not per
# time, because snapshots may be irregular. ``analyze_trends`` normalises
# x to its index, so a slope of 0.01 means "avg_uplift grows by 0.01 per
# snapshot we take". Treat as relative.
IMPROVING_SLOPE = 0.005
DEGRADING_SLOPE = -0.005
MIN_SAMPLES_FOR_TREND = 3


@dataclass(frozen=True)
class PatternTrend:
    pattern_id: str
    samples: int
    latest_n: int
    latest_uplift: float
    latest_win_rate: float
    slope: float
    trend: str  # "improving" | "flat" | "degrading" | "insufficient"
    last_seen: str | None


def fetch_stats(url: str = DEFAULT_STATS_URL, timeout: int = 10) -> dict[str, Any]:
    """GET /wiki/v1/stats — no auth needed."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def snapshot_stats(
    payload: dict[str, Any], history_dir: Path | None = None
) -> Path:
    """Persist a single stats payload with an ISO-timestamp filename."""
    history_dir = history_dir or STATS_HISTORY_DIR
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = history_dir / f"stats-{ts}.json"
    out.write_text(
        json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "stats": payload,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Snapshot stats: %d patterns → %s", len(payload), out.name)
    return out


def load_history(history_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return all snapshots, sorted by filename (= timestamp lexicographic)."""
    history_dir = history_dir or STATS_HISTORY_DIR
    if not history_dir.exists():
        return []
    snaps: list[dict[str, Any]] = []
    for fp in sorted(history_dir.glob("stats-*.json")):
        try:
            snaps.append(json.loads(fp.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable snapshot %s: %s", fp.name, exc)
    return snaps


def _linear_slope(ys: list[float]) -> float:
    """Closed-form slope of y = a*x + b with x = 0..n-1.

    Equivalent to ``statistics.linear_regression`` (3.10+), but written out
    so we don't depend on that arrangement of imports.
    """
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = statistics.fmean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


def _classify(slope: float, samples: int) -> str:
    if samples < MIN_SAMPLES_FOR_TREND:
        return "insufficient"
    if slope > IMPROVING_SLOPE:
        return "improving"
    if slope < DEGRADING_SLOPE:
        return "degrading"
    return "flat"


def analyze_trends(
    snapshots: Iterable[dict[str, Any]], *, window: int = 10
) -> dict[str, PatternTrend]:
    """Linear-regression slope per pattern over the last ``window`` snapshots."""
    snaps = list(snapshots)
    if not snaps:
        return {}
    use = snaps[-window:] if len(snaps) > window else snaps

    # Per-pattern series of (uplift_mean, n, win_rate, last_seen)
    series: dict[str, list[tuple[float, int, float, str | None]]] = {}
    for snap in use:
        stats = snap.get("stats", {}) or {}
        for pid, agg in stats.items():
            uplift = float(agg.get("avg_uplift", 0.0))
            n_total = int(agg.get("n", 0))
            wr = float(agg.get("win_rate", 0.0))
            last = agg.get("last_seen_iso")
            series.setdefault(pid, []).append((uplift, n_total, wr, last))

    out: dict[str, PatternTrend] = {}
    for pid, samples in series.items():
        ys = [u for (u, _n, _w, _l) in samples]
        latest = samples[-1]
        slope = _linear_slope(ys)
        out[pid] = PatternTrend(
            pattern_id=pid,
            samples=len(samples),
            latest_n=latest[1],
            latest_uplift=round(latest[0], 4),
            latest_win_rate=round(latest[2], 4),
            slope=round(slope, 6),
            trend=_classify(slope, len(samples)),
            last_seen=latest[3],
        )
    return out


def render_markdown_report(trends: dict[str, PatternTrend]) -> str:
    """Pretty multi-section markdown grouped by trend label."""
    by_trend: dict[str, list[PatternTrend]] = {}
    for t in trends.values():
        by_trend.setdefault(t.trend, []).append(t)

    out = ["# Pattern uplift trends\n"]
    out.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    out.append(f"Patterns tracked: **{len(trends)}**\n")

    section_order = ["degrading", "improving", "flat", "insufficient"]
    for label in section_order:
        entries = by_trend.get(label, [])
        if not entries:
            continue
        entries.sort(key=lambda t: t.slope)  # most-negative first within section
        out.append(f"\n## {label.title()} ({len(entries)})\n")
        out.append("| pattern_id | samples | latest_n | latest_uplift | slope | win_rate |")
        out.append("|---|---|---|---|---|---|")
        for t in entries:
            out.append(
                f"| `{t.pattern_id}` | {t.samples} | {t.latest_n} | "
                f"{t.latest_uplift:+.3f} | {t.slope:+.4f} | {t.latest_win_rate:.2f} |"
            )
    return "\n".join(out) + "\n"


def run(
    *,
    url: str = DEFAULT_STATS_URL,
    snapshot_now: bool = True,
    history_dir: Path | None = None,
    out_dir: Path | None = None,
    window: int = 10,
) -> dict[str, Any]:
    """End-to-end: optionally snapshot + analyze + write report."""
    history_dir = history_dir or STATS_HISTORY_DIR
    out_dir = out_dir or REPORTS_DIR

    if snapshot_now:
        try:
            payload = fetch_stats(url)
            snapshot_stats(payload, history_dir=history_dir)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("Could not fetch /stats from %s: %s", url, exc)

    snaps = load_history(history_dir=history_dir)
    trends = analyze_trends(snaps, window=window)

    out_dir.mkdir(parents=True, exist_ok=True)
    trends_json = out_dir / "trends.json"
    trends_json.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "snapshots_used": min(len(snaps), window),
                "patterns": {pid: t.__dict__ for pid, t in trends.items()},
            },
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    md_path = out_dir / "pattern_trends.md"
    md_path.write_text(render_markdown_report(trends), encoding="utf-8")
    logger.info(
        "Trends analysed across %d snapshots → %s + %s",
        len(snaps), trends_json.name, md_path.name,
    )
    return {
        "snapshots": len(snaps),
        "patterns_tracked": len(trends),
        "trends_json": str(trends_json),
        "report_md": str(md_path),
    }


__all__ = [
    "DEGRADING_SLOPE",
    "IMPROVING_SLOPE",
    "MIN_SAMPLES_FOR_TREND",
    "PatternTrend",
    "STATS_HISTORY_DIR",
    "REPORTS_DIR",
    "analyze_trends",
    "fetch_stats",
    "load_history",
    "render_markdown_report",
    "run",
    "snapshot_stats",
]
