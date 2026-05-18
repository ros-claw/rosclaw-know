"""Bridge-index reweighting — fold pattern_metrics back into the bridge.

Phase 4 step 2: after :mod:`rosclaw_know.feedback_distill` produces
``data/assets/pattern_metrics.json``, this module merges the per-pattern
uplift signals back into ``data/assets/bridge_index.json`` so the runtime
(:mod:`rosclaw_how.semantic_router`) can demote losers and surface winners.

Aggregation rule per cluster:

  uplift_mean(cluster) =
      Σ pattern.n * pattern.uplift_mean  /  Σ pattern.n
                        over patterns in cluster.associated_patterns

  uplift_n(cluster)    = Σ pattern.n
  win_rate(cluster)    = Σ pattern.n * pattern.win_rate / Σ pattern.n
  priority(cluster)    = -1 iff every contributing pattern is demoted
                              AND uplift_n(cluster) ≥ MIN_SAMPLE_SIZE
                         else unset (default in runtime)

Patterns with zero samples contribute nothing — they are neither rewarded
nor penalised. The runtime treats ``priority == -1`` clusters as "do not
inject" (semantic_router.find_nearest skips them in the top-k loop).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import ASSETS_DIR
from .feedback_distill import MIN_SAMPLE_SIZE, PatternMetric, is_demoted

logger = logging.getLogger("rosclaw_know.bridge_reweighter")


def _load_metrics(path: Path) -> dict[str, PatternMetric]:
    """Read pattern_metrics.json back into PatternMetric instances."""
    if not path.exists():
        logger.warning("pattern_metrics.json not found at %s — nothing to reweight", path)
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, PatternMetric] = {}
    for pid, entry in payload.get("patterns", {}).items():
        try:
            out[pid] = PatternMetric(**entry)
        except TypeError as exc:
            logger.warning("Skipping malformed metric for %s: %s", pid, exc)
    return out


def _aggregate_for_cluster(
    pattern_ids: list[str], metrics: dict[str, PatternMetric]
) -> dict[str, float | int] | None:
    """Compute n-weighted uplift_mean and win_rate over a cluster's patterns.

    Returns ``None`` when no contributing pattern has any samples — we want
    the cluster to look unscored, not zero-scored.
    """
    contribs = [metrics[pid] for pid in pattern_ids if pid in metrics and metrics[pid].n > 0]
    if not contribs:
        return None
    total_n = sum(m.n for m in contribs)
    if total_n == 0:
        return None
    uplift_mean = sum(m.n * m.uplift_mean for m in contribs) / total_n
    win_rate = sum(m.n * m.win_rate for m in contribs) / total_n
    return {
        "uplift_mean": round(uplift_mean, 4),
        "uplift_n": int(total_n),
        "win_rate": round(win_rate, 4),
    }


def _every_contrib_demoted(
    pattern_ids: list[str], metrics: dict[str, PatternMetric]
) -> bool:
    """True iff EVERY pattern in the cluster has enough negative signal.

    All-or-nothing protects clusters where some patterns are still winning;
    a single positive signal blocks demotion.
    """
    contribs = [metrics[pid] for pid in pattern_ids if pid in metrics and metrics[pid].n > 0]
    if not contribs:
        return False
    return all(is_demoted(m) for m in contribs)


def reweight_bridge_index(
    bridge_path: Path | None = None,
    metrics_path: Path | None = None,
) -> dict[str, int]:
    """Apply pattern_metrics to bridge_index in place. Returns a stats dict.

    The bridge JSON is rewritten only if at least one cluster changes — keeps
    file mtime stable when there is no signal yet (lets rosclaw-how skip
    the corresponding asset reload).
    """
    bridge_path = bridge_path or (ASSETS_DIR / "bridge_index.json")
    metrics_path = metrics_path or (ASSETS_DIR / "pattern_metrics.json")

    if not bridge_path.exists():
        logger.warning("bridge_index.json not found at %s — abort reweight", bridge_path)
        return {"clusters_touched": 0, "clusters_demoted": 0, "clusters_total": 0}

    metrics = _load_metrics(metrics_path)
    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    clusters = bridge.get("symptom_clusters", {}) or {}

    touched = 0
    demoted = 0
    for cluster_id, cluster in clusters.items():
        pids: list[str] = list(cluster.get("associated_patterns") or [])
        agg = _aggregate_for_cluster(pids, metrics)
        if agg is None:
            # No samples for this cluster yet — clear stale stats (idempotent).
            for stale in ("uplift_mean", "uplift_n", "win_rate", "priority"):
                if stale in cluster:
                    del cluster[stale]
                    touched += 1
            continue

        # Merge new stats — only mark touched if a value actually changed.
        for k, v in agg.items():
            if cluster.get(k) != v:
                cluster[k] = v
                touched += 1

        if agg["uplift_n"] >= MIN_SAMPLE_SIZE and _every_contrib_demoted(pids, metrics):
            if cluster.get("priority") != -1:
                cluster["priority"] = -1
                touched += 1
                demoted += 1
        else:
            if cluster.get("priority") == -1:
                del cluster["priority"]
                touched += 1

    if touched:
        bridge_path.write_text(
            json.dumps(bridge, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Reweighted bridge_index: %d field updates across %d clusters; %d soft-deprecated.",
            touched, len(clusters), demoted,
        )
    else:
        logger.info("No reweight changes; bridge_index.json untouched.")

    return {
        "clusters_touched": touched,
        "clusters_demoted": demoted,
        "clusters_total": len(clusters),
    }


__all__ = ["reweight_bridge_index"]
