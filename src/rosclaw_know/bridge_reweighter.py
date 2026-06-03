"""Bridge-index reweighting — fold pattern_metrics back into the bridge.

Phase 4 step 2: after :mod:`rosclaw_know.feedback_distill` produces
``data/assets/pattern_metrics.json``, this module merges the per-pattern
uplift signals back into ``data/assets/bridge_index.json`` so the runtime
(:mod:`rosclaw_how.semantic_router`) can demote losers and surface winners.

Sprint 6 upgrade (plan §11.8): when
``data/assets/evidence_stats.json`` is present, promotion / demotion
decisions are driven by **placebo_adjusted_uplift** (true arm minus
placebo arm) instead of raw uplift.  Plan §Sprint 6 acceptance:
"pattern 晋级不能只看 raw uplift，要看 adjusted uplift".  The
v1 ``pattern_metrics.json`` path is still consulted for backwards
compat with Phase 4 deployments that haven't migrated to v2 traces yet.

Aggregation rule per cluster:

  uplift_mean(cluster) =
      Σ pattern.n * pattern.uplift_mean  /  Σ pattern.n
                        over patterns in cluster.associated_patterns

  uplift_n(cluster)    = Σ pattern.n
  win_rate(cluster)    = Σ pattern.n * pattern.win_rate / Σ pattern.n
  priority(cluster)    = -1 iff every contributing pattern is demoted
                              AND uplift_n(cluster) ≥ MIN_SAMPLE_SIZE
                         else unset (default in runtime)

In Sprint-6 mode the same aggregation runs over the **true-arm**
``avg_uplift_5`` instead of raw ``uplift_mean``, and the demotion test
uses ``placebo_adjusted_uplift`` per pattern (see
:func:`evidence_distill.is_demoted`).

Patterns with zero samples contribute nothing — they are neither rewarded
nor penalised. The runtime treats ``priority == -1`` clusters as "do not
inject" (semantic_router.find_nearest skips them in the top-k loop).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .config import ASSETS_DIR
from .evidence_distill import (
    EvidenceStat,
)
from .evidence_distill import (
    is_demoted as v2_is_demoted,
)
from .evidence_distill import (
    is_promoted as v2_is_promoted,
)
from .feedback_distill import MIN_SAMPLE_SIZE, PatternMetric, is_demoted

if TYPE_CHECKING:  # pragma: no cover — circular-import guard
    from .evidence_distill import CoverageReport
    from .schemas import EvidenceTrace

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


def _load_evidence_stats(path: Path) -> dict[str, EvidenceStat] | None:
    """Read ``evidence_stats.json`` back into :class:`EvidenceStat` objects.

    Returns ``None`` (not ``{}``) when the file is missing — that's how
    :func:`reweight_bridge_index` decides whether to take the v2 path.
    Returning the empty dict is reserved for "v2 stats present but no
    pattern has samples", which is a different situation: the v2 path
    is still authoritative but contributes no decisions.
    """
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, EvidenceStat] = {}
    for pid, entry in payload.get("patterns", {}).items():
        try:
            # The serialised form has a few extra fields (is_promoted /
            # is_demoted derivatives) — drop them before reconstructing.
            entry = {
                k: v for k, v in entry.items()
                if k in {
                    "pattern_id", "n", "n_by_arm", "by_arm",
                    "hint_use_rate", "placebo_adjusted_uplift",
                    "shuffled_adjusted_uplift", "raw_uplift_mean",
                    "last_seen",
                }
            }
            # by_arm field is dict[arm → asdict(ArmStats)] in the JSON;
            # we reconstruct lazily on demand via .get() rather than
            # rebuilding ArmStats objects, since the bridge_reweighter
            # only needs raw_uplift_mean + n_by_arm + placebo_adjusted.
            out[pid] = _build_evidence_stat(entry)
        except (TypeError, KeyError) as exc:
            logger.warning("Skipping malformed evidence stat for %s: %s", pid, exc)
    return out


def _build_evidence_stat(entry: dict) -> EvidenceStat:
    """Reconstruct an :class:`EvidenceStat` from its JSON serialisation."""
    from .evidence_distill import ArmStats
    by_arm = {
        a: ArmStats(**s) for a, s in entry.get("by_arm", {}).items()
    }
    return EvidenceStat(
        pattern_id=entry["pattern_id"],
        n=int(entry.get("n", 0)),
        n_by_arm=dict(entry.get("n_by_arm", {})),
        by_arm=by_arm,
        hint_use_rate=float(entry.get("hint_use_rate", 0.0)),
        placebo_adjusted_uplift=entry.get("placebo_adjusted_uplift"),
        shuffled_adjusted_uplift=entry.get("shuffled_adjusted_uplift"),
        raw_uplift_mean=float(entry.get("raw_uplift_mean", 0.0)),
        last_seen=str(entry.get("last_seen", "")),
    )


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
    evidence_stats_path: Path | None = None,
    *,
    force_v1: bool = False,
) -> dict[str, int]:
    """Apply pattern_metrics to bridge_index in place. Returns a stats dict.

    Sprint 6: when ``data/assets/evidence_stats.json`` is present and
    ``force_v1`` is False, the function uses **v2** statistics
    (placebo-adjusted uplift) for promotion / demotion decisions instead
    of v1 ``pattern_metrics.json``.  The v1 file is still consulted as a
    fallback for clusters whose patterns don't have v2 stats yet.

    The bridge JSON is rewritten only if at least one cluster changes — keeps
    file mtime stable when there is no signal yet (lets rosclaw-how skip
    the corresponding asset reload).
    """
    bridge_path = bridge_path or (ASSETS_DIR / "bridge_index.json")
    metrics_path = metrics_path or (ASSETS_DIR / "pattern_metrics.json")
    evidence_stats_path = evidence_stats_path or (ASSETS_DIR / "evidence_stats.json")

    if not bridge_path.exists():
        logger.warning("bridge_index.json not found at %s — abort reweight", bridge_path)
        return {
            "clusters_touched": 0, "clusters_demoted": 0,
            "clusters_promoted": 0, "clusters_total": 0,
            "mode": "v1",
        }

    evidence_stats = None if force_v1 else _load_evidence_stats(evidence_stats_path)
    if evidence_stats is not None:
        return _reweight_with_evidence_v2(
            bridge_path, evidence_stats, metrics_path,
        )

    return _reweight_v1(bridge_path, metrics_path)


def _reweight_v1(bridge_path: Path, metrics_path: Path) -> dict[str, int]:
    """Original Phase 4 reweight path (raw uplift only)."""
    metrics = _load_metrics(metrics_path)
    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    clusters = bridge.get("symptom_clusters", {}) or {}

    touched = 0
    demoted = 0
    for _cluster_id, cluster in clusters.items():
        pids: list[str] = list(cluster.get("associated_patterns") or [])
        agg = _aggregate_for_cluster(pids, metrics)
        if agg is None:
            for stale in ("uplift_mean", "uplift_n", "win_rate", "priority"):
                if stale in cluster:
                    del cluster[stale]
                    touched += 1
            continue

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
            "Reweighted bridge_index (v1): %d field updates across %d clusters; %d soft-deprecated.",
            touched, len(clusters), demoted,
        )
    else:
        logger.info("No reweight changes; bridge_index.json untouched.")

    return {
        "clusters_touched": touched,
        "clusters_demoted": demoted,
        "clusters_promoted": 0,
        "clusters_total": len(clusters),
        "mode": "v1",
    }


# ── v2 aggregation (Sprint 6) ───────────────────────────────────────────


def _aggregate_for_cluster_v2(
    pattern_ids: list[str],
    stats: dict[str, EvidenceStat],
) -> dict[str, float | int] | None:
    """Compute n-weighted true-arm uplift + placebo-adjusted uplift.

    Returns ``None`` when no contributing pattern has any true-arm
    samples — we want the cluster to look unscored.
    """
    contribs = [
        stats[pid] for pid in pattern_ids
        if pid in stats and stats[pid].n_by_arm.get("true", 0) > 0
    ]
    if not contribs:
        return None
    total_true_n = sum(s.n_by_arm["true"] for s in contribs)
    if total_true_n == 0:
        return None
    uplift_5 = [
        (s.n_by_arm["true"], s.by_arm["true"].avg_uplift_5)
        for s in contribs
        if s.by_arm["true"].avg_uplift_5 is not None
    ]
    if uplift_5:
        denom = sum(n for n, _ in uplift_5)
        uplift_mean = sum(n * u for n, u in uplift_5) / denom
    else:
        uplift_mean = 0.0
    win_rates = [
        (s.n_by_arm["true"], s.by_arm["true"].win_rate)
        for s in contribs
    ]
    win_rate = (
        sum(n * w for n, w in win_rates) / total_true_n
        if win_rates else 0.0
    )
    # placebo-adjusted uplift — n-weighted over contributors that have
    # both a true and a placebo arm.
    adj_pairs = [
        (s.n_by_arm["true"], s.placebo_adjusted_uplift)
        for s in contribs
        if s.placebo_adjusted_uplift is not None
    ]
    if adj_pairs:
        denom_adj = sum(n for n, _ in adj_pairs)
        adj_uplift = sum(n * u for n, u in adj_pairs) / denom_adj
    else:
        adj_uplift = None
    out: dict[str, float | int] = {
        "uplift_mean": round(uplift_mean, 4),
        "uplift_n": int(total_true_n),
        "win_rate": round(win_rate, 4),
    }
    if adj_uplift is not None:
        out["placebo_adjusted_uplift"] = round(adj_uplift, 4)
    return out


def _every_contrib_demoted_v2(
    pattern_ids: list[str], stats: dict[str, EvidenceStat]
) -> bool:
    contribs = [
        stats[pid] for pid in pattern_ids
        if pid in stats and stats[pid].n_by_arm.get("true", 0) > 0
    ]
    if not contribs:
        return False
    return all(v2_is_demoted(s) for s in contribs)


def _every_contrib_promoted_v2(
    pattern_ids: list[str], stats: dict[str, EvidenceStat]
) -> bool:
    contribs = [
        stats[pid] for pid in pattern_ids
        if pid in stats and stats[pid].n_by_arm.get("true", 0) > 0
    ]
    if not contribs:
        return False
    return all(v2_is_promoted(s) for s in contribs)


def _reweight_with_evidence_v2(
    bridge_path: Path,
    stats: dict[str, EvidenceStat],
    metrics_path: Path,
) -> dict[str, int]:
    """Sprint-6 reweight: drive promote/demote off placebo-adjusted uplift.

    For clusters whose patterns have no v2 stats yet, fall back to v1
    pattern_metrics so callers can do a partial rollout.
    """
    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    clusters = bridge.get("symptom_clusters", {}) or {}
    v1_metrics = _load_metrics(metrics_path)

    touched = 0
    demoted = 0
    promoted = 0
    for _cluster_id, cluster in clusters.items():
        pids: list[str] = list(cluster.get("associated_patterns") or [])

        v2_match = [pid for pid in pids if pid in stats]
        if v2_match:
            agg = _aggregate_for_cluster_v2(pids, stats)
        else:
            # Fallback to v1 metrics for this cluster.
            agg = _aggregate_for_cluster(pids, v1_metrics)

        if agg is None:
            for stale in (
                "uplift_mean", "uplift_n", "win_rate", "priority",
                "placebo_adjusted_uplift",
            ):
                if stale in cluster:
                    del cluster[stale]
                    touched += 1
            continue

        for k, v in agg.items():
            if cluster.get(k) != v:
                cluster[k] = v
                touched += 1

        if v2_match:
            if (
                agg.get("uplift_n", 0) >= MIN_SAMPLE_SIZE
                and _every_contrib_promoted_v2(pids, stats)
            ):
                if cluster.get("priority") != 1:
                    cluster["priority"] = 1
                    touched += 1
                    promoted += 1
            elif (
                agg.get("uplift_n", 0) >= MIN_SAMPLE_SIZE
                and _every_contrib_demoted_v2(pids, stats)
            ):
                if cluster.get("priority") != -1:
                    cluster["priority"] = -1
                    touched += 1
                    demoted += 1
            else:
                if cluster.get("priority") in (-1, 1):
                    del cluster["priority"]
                    touched += 1
        else:
            # v1 fallback for this cluster
            if (
                agg.get("uplift_n", 0) >= MIN_SAMPLE_SIZE
                and _every_contrib_demoted(pids, v1_metrics)
            ):
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
            "Reweighted bridge_index (v2): %d field updates across %d clusters; %d promoted, %d demoted.",
            touched, len(clusters), promoted, demoted,
        )
    else:
        logger.info("No reweight changes (v2); bridge_index.json untouched.")

    return {
        "clusters_touched": touched,
        "clusters_demoted": demoted,
        "clusters_promoted": promoted,
        "clusters_total": len(clusters),
        "mode": "v2",
    }


# ── Sprint 12: in-memory direct path ────────────────────────────────────


def reweight_bridge_index_from_stats(
    stats: Mapping[str, EvidenceStat],
    *,
    bridge_path: Path | None = None,
    metrics_path: Path | None = None,
) -> dict[str, int]:
    """Reweight bridge_index using already-distilled in-memory stats.

    Sprint 12 contract: identical bridge_index output to
    :func:`reweight_bridge_index` when the latter is pointed at an
    ``evidence_stats.json`` file produced from the same stats — but
    without writing/reading that intermediate file.

    Parameters
    ----------
    stats
        ``dict[pattern_id, EvidenceStat]``.  Typically the first element
        of :func:`evidence_distill.distill`'s return value, or
        deserialised from ``evidence_stats.json`` for a what-if run.
    bridge_path
        Where ``bridge_index.json`` lives.  Defaults to
        ``data/assets/bridge_index.json``.
    metrics_path
        ``pattern_metrics.json`` for v1 fallback.  Defaults to
        ``data/assets/pattern_metrics.json``.

    Returns
    -------
    dict[str, int]
        Same summary shape as :func:`reweight_bridge_index`
        (``clusters_touched``, ``_demoted``, ``_promoted``, ``_total``,
        ``mode``).  ``mode`` is always ``"v2"`` since we have v2 stats.
    """
    bridge_path = bridge_path or (ASSETS_DIR / "bridge_index.json")
    metrics_path = metrics_path or (ASSETS_DIR / "pattern_metrics.json")
    if not bridge_path.exists():
        logger.warning(
            "bridge_index.json not found at %s — abort reweight",
            bridge_path,
        )
        return {
            "clusters_touched": 0, "clusters_demoted": 0,
            "clusters_promoted": 0, "clusters_total": 0,
            "mode": "v2",
        }
    return _reweight_with_evidence_v2(bridge_path, dict(stats), metrics_path)


def reweight_bridge_index_from_traces(
    traces: Iterable[EvidenceTrace],
    *,
    bridge_path: Path | None = None,
    metrics_path: Path | None = None,
) -> tuple[dict[str, int], CoverageReport]:
    """Sprint 12 convenience: distill traces in-memory, then reweight.

    Identical to running :func:`evidence_distill.distill` followed by
    :func:`reweight_bridge_index_from_stats`, packaged as one call.

    Returns ``(summary, coverage_report)`` — the coverage report is
    returned alongside the reweight summary so callers can surface
    Sprint 6's coverage gates (injection_id missing, post_score_3/5
    coverage, code_diff_summary coverage) without re-distilling.
    """
    from .evidence_distill import distill

    stats, coverage = distill(traces)
    summary = reweight_bridge_index_from_stats(
        stats,
        bridge_path=bridge_path,
        metrics_path=metrics_path,
    )
    return summary, coverage


__all__ = [
    "reweight_bridge_index",
    "reweight_bridge_index_from_stats",
    "reweight_bridge_index_from_traces",
]
