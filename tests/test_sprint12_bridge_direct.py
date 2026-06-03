"""Sprint 12: in-memory direct reweight path.

Sprint 11 wired the real-robot evidence loop via an intermediate file
(``evidence_stats.json``).  Sprint 12 closes the gap: feed
``dict[str, EvidenceStat]`` (or even raw ``EvidenceTrace`` lists)
straight into ``bridge_reweighter`` so callers can run the full
"real-robot ingest → bridge_index update" cycle without touching disk
for intermediates.

The acceptance gate is identity: the in-memory path and the
file-based path MUST produce byte-for-byte identical bridge_index.json
content given the same trace input.
"""
from __future__ import annotations

import json
from pathlib import Path

from rosclaw_know.bridge_reweighter import (
    reweight_bridge_index,
    reweight_bridge_index_from_stats,
    reweight_bridge_index_from_traces,
)
from rosclaw_know.evidence_distill import distill, write_stats
from rosclaw_know.sim_ingest import (
    events_to_evidence_traces,
    read_robot_event_jsonl,
    reweight_bridge_from_robot_events,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "sprint11"

PATTERN_ID = "compiled_zero_integral_gain_on_saturation"


# ── tiny test fixture: synthetic bridge_index ──────────────────────────────


def _make_bridge_index(tmp: Path, cluster_pattern: str = PATTERN_ID) -> Path:
    """Write a minimal bridge_index.json containing ONE cluster that
    associates the Sprint 11 promoted pattern.

    The v2 catalog uses ``compiled_*`` ids; the legacy bridge_index uses
    ``pattern_*``.  For Sprint 12 we need a cluster that lists a
    compiled_* id so the reweight has somewhere to write.
    """
    bridge = {
        "schema_version": "2.0",
        "symptom_clusters": {
            "windup_test_cluster": {
                "id": "windup_test_cluster",
                "associated_patterns": [cluster_pattern],
                "symptom_text": "Test cluster for Sprint 12.",
            },
            # Unrelated cluster — should remain untouched.
            "unrelated_cluster": {
                "id": "unrelated_cluster",
                "associated_patterns": ["pattern_does_not_exist"],
                "symptom_text": "Unrelated.",
            },
        },
        "safety_label_index": {},
    }
    path = tmp / "bridge_index.json"
    path.write_text(json.dumps(bridge, indent=2) + "\n", encoding="utf-8")
    return path


def _empty_metrics_file(tmp: Path) -> Path:
    """Stub the v1 ``pattern_metrics.json`` — Sprint 12 doesn't depend
    on it, but the reweighter still tries to read it for fallback."""
    p = tmp / "pattern_metrics.json"
    p.write_text(json.dumps({
        "schema_version": "1.0",
        "win_delta_threshold": 0.05,
        "min_sample_size": 5,
        "patterns": {},
    }), encoding="utf-8")
    return p


# ── in-memory direct path ────────────────────────────────────────────────


def test_direct_path_promotes_cluster(tmp_path: Path) -> None:
    """In-memory path: traces → distill → reweight_bridge_index_from_stats."""
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)

    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    stats, _coverage = distill(traces)

    summary = reweight_bridge_index_from_stats(
        stats,
        bridge_path=bridge,
        metrics_path=metrics,
    )
    assert summary["mode"] == "v2"
    assert summary["clusters_promoted"] == 1

    reloaded = json.loads(bridge.read_text())
    cluster = reloaded["symptom_clusters"]["windup_test_cluster"]
    assert cluster["priority"] == 1
    assert "placebo_adjusted_uplift" in cluster
    assert cluster["placebo_adjusted_uplift"] > 0.03


def test_direct_path_leaves_unrelated_clusters_alone(tmp_path: Path) -> None:
    """Unrelated clusters lose their stale priority but stay structurally intact."""
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)

    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    stats, _ = distill(events_to_evidence_traces(evs))
    reweight_bridge_index_from_stats(stats, bridge_path=bridge, metrics_path=metrics)

    reloaded = json.loads(bridge.read_text())
    unrelated = reloaded["symptom_clusters"]["unrelated_cluster"]
    # Unrelated cluster has no v2 stats AND no v1 metrics → no priority,
    # no aggregate fields.
    assert "priority" not in unrelated
    assert "placebo_adjusted_uplift" not in unrelated


def test_direct_path_demotes_when_true_underperforms_placebo(tmp_path: Path) -> None:
    """If the in-memory stats say demote, the bridge cluster's priority = -1."""
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)

    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    # Flip arms so true under-performs.
    flipped = []
    for t in traces:
        if t.arm == "true":
            t = t.model_copy(update={"best_delta_5": 0.02})
        elif t.arm == "placebo":
            t = t.model_copy(update={"best_delta_5": 0.30})
        flipped.append(t)
    stats, _ = distill(flipped)

    summary = reweight_bridge_index_from_stats(
        stats, bridge_path=bridge, metrics_path=metrics,
    )
    assert summary["clusters_demoted"] == 1

    reloaded = json.loads(bridge.read_text())
    cluster = reloaded["symptom_clusters"]["windup_test_cluster"]
    assert cluster["priority"] == -1


# ── parity: in-memory vs disk path ────────────────────────────────────────


def test_direct_path_matches_disk_path_byte_for_byte(tmp_path: Path) -> None:
    """The same traces yield byte-identical bridge_index.json via either path."""
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)
    stats, coverage = distill(traces)

    # Disk path: write evidence_stats.json then reweight reading it back.
    disk_dir = tmp_path / "disk"
    disk_dir.mkdir()
    bridge_disk = _make_bridge_index(disk_dir)
    metrics_disk = _empty_metrics_file(disk_dir)
    ev_path = disk_dir / "evidence_stats.json"
    write_stats(stats, coverage, out_path=ev_path)
    reweight_bridge_index(
        bridge_path=bridge_disk,
        metrics_path=metrics_disk,
        evidence_stats_path=ev_path,
    )

    # In-memory path: feed stats directly.
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    bridge_mem = _make_bridge_index(mem_dir)
    metrics_mem = _empty_metrics_file(mem_dir)
    reweight_bridge_index_from_stats(
        stats, bridge_path=bridge_mem, metrics_path=metrics_mem,
    )

    assert bridge_disk.read_bytes() == bridge_mem.read_bytes()


# ── higher-level: from_traces ────────────────────────────────────────────


def test_from_traces_returns_summary_and_coverage(tmp_path: Path) -> None:
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)
    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    traces = events_to_evidence_traces(evs)

    summary, coverage = reweight_bridge_index_from_traces(
        traces, bridge_path=bridge, metrics_path=metrics,
    )
    assert summary["mode"] == "v2"
    assert summary["clusters_promoted"] == 1
    # Coverage is the same object distill() would return.
    assert coverage.violations == []
    # Bridge was actually updated.
    cluster = json.loads(bridge.read_text())["symptom_clusters"]["windup_test_cluster"]
    assert cluster["priority"] == 1


def test_from_traces_with_no_traces_is_a_noop(tmp_path: Path) -> None:
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)

    summary, _ = reweight_bridge_index_from_traces(
        [], bridge_path=bridge, metrics_path=metrics,
    )
    # Empty traces still process — but no v2 stats means cluster's
    # priority drops (file may or may not be touched depending on
    # whether any stale fields exist).  Either way, no promote/demote.
    assert summary["clusters_promoted"] == 0
    assert summary["clusters_demoted"] == 0
    # Bridge content is either untouched or pruned of stale fields.
    assert bridge.exists()


# ── end-to-end one-liner: RobotEvent → bridge update ─────────────────────


def test_end_to_end_from_robot_events(tmp_path: Path) -> None:
    """One call: rosbag JSONL → bridge_index update.  No intermediate files."""
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)

    evs = read_robot_event_jsonl(FIX / "robot_traces_with_evidence.jsonl")
    summary, coverage = reweight_bridge_from_robot_events(
        evs,
        bridge_path=bridge,
        metrics_path=metrics,
    )
    assert summary["clusters_promoted"] == 1
    assert coverage.violations == []
    cluster = json.loads(bridge.read_text())["symptom_clusters"]["windup_test_cluster"]
    assert cluster["priority"] == 1


def test_end_to_end_from_robot_events_skips_when_no_envelope(tmp_path: Path) -> None:
    """RobotEvents without task_run envelopes produce no traces → noop."""
    bridge = _make_bridge_index(tmp_path)
    metrics = _empty_metrics_file(tmp_path)
    from rosclaw_know.sim_ingest.event_schema import RobotEvent

    no_envelope = [
        RobotEvent(timestamp="t", event_type="collision",
                   embodiment_id="ur5", severity="warning",
                   fingerprint="x", fields={}, source="rosbag", source_id="x"),
    ]
    summary, _ = reweight_bridge_from_robot_events(
        no_envelope, bridge_path=bridge, metrics_path=metrics,
    )
    assert summary["clusters_promoted"] == 0


# ── no-op contract: missing bridge file ──────────────────────────────────


def test_direct_path_logs_warning_on_missing_bridge(tmp_path: Path) -> None:
    """When bridge_index.json is missing, the call is a graceful noop."""
    metrics = _empty_metrics_file(tmp_path)
    # We deliberately do NOT create the bridge file.
    bogus = tmp_path / "missing_bridge.json"
    summary = reweight_bridge_index_from_stats(
        {}, bridge_path=bogus, metrics_path=metrics,
    )
    assert summary["clusters_touched"] == 0
    assert summary["clusters_total"] == 0
    assert not bogus.exists()


# ── exports ──────────────────────────────────────────────────────────────


def test_module_exports_new_symbols() -> None:
    """Sprint 12 surface: the two new functions are importable from the package."""
    import rosclaw_know.bridge_reweighter as br
    assert hasattr(br, "reweight_bridge_index_from_stats")
    assert hasattr(br, "reweight_bridge_index_from_traces")
    # The Sprint 11+12 one-liner lives in the sim_ingest namespace.
    import rosclaw_know.sim_ingest as si
    assert hasattr(si, "reweight_bridge_from_robot_events")
