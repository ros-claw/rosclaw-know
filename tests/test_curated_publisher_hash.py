"""Tests for the P0 content_hash work in curated_publisher.

See docs/know-how下一步建议.md §4.1.1 — same routing-critical inputs must
produce the same hash; any routing-critical change must produce a new hash;
ephemeral fields must NOT affect the hash.
"""
from __future__ import annotations

from rosclaw_know.curated_publisher import (
    ROUTING_CRITICAL_FIELDS,
    compute_cluster_content_hash,
)


_BASE = {
    "standard_name": "PID integral wind-up drives actuator into torque saturation",
    "domain": "Control_Locomotion",
    "source": "curated",
    "source_tier": "S_CURATED_VERIFIED",
    "safety_label": "Torque_Overflow",
    "matched_keywords": ["pid", "torque", "windup"],
    "associated_patterns": ["anti_windup_pid"],
    "cross_domain_analogies": [
        {
            "source_domain": "Systems_Compute",
            "insight": "back-pressure",
            "action_suggestion": "pause integrator",
            "neighbor_id": "curated",
        },
    ],
}


def test_routing_critical_fields_documented():
    """Each field documented in §4.1.1 must be present in the tuple."""
    expected = {
        "standard_name",
        "domain",
        "topic_group",
        "topic_tag",
        "matched_keywords",
        "cross_domain_analogies",
        "associated_patterns",
        "source",
        "source_tier",
        "priority",
        "snippet_mode_hint",
    }
    assert expected.issubset(set(ROUTING_CRITICAL_FIELDS))


def test_same_input_same_hash():
    a = compute_cluster_content_hash(dict(_BASE))
    b = compute_cluster_content_hash(dict(_BASE))
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_list_order_does_not_affect_hash():
    a = compute_cluster_content_hash(_BASE)
    shuffled = dict(_BASE)
    shuffled["matched_keywords"] = ["windup", "pid", "torque"]
    b = compute_cluster_content_hash(shuffled)
    assert a == b


def test_routing_critical_change_changes_hash():
    a = compute_cluster_content_hash(_BASE)

    cases = [
        ("standard_name", "different name"),
        ("domain", "Systems_Compute"),
        ("source", "muse"),
        ("source_tier", "C_MUSE_SYNTH"),
        ("safety_label", "Memory_Exhaustion"),
        ("matched_keywords", ["unrelated"]),
        ("associated_patterns", ["other_pattern"]),
    ]
    for field, new_value in cases:
        mutated = dict(_BASE)
        mutated[field] = new_value
        assert compute_cluster_content_hash(mutated) != a, (
            f"changing {field} must change content_hash"
        )


def test_new_routing_critical_field_changes_hash():
    """Adding a routing-critical field where there wasn't one before
    must produce a different hash (the absent-vs-present distinction
    matters for delta-path correctness on reload)."""
    a = compute_cluster_content_hash(_BASE)
    with_topic = dict(_BASE)
    with_topic["topic_group"] = "pid_control"
    assert compute_cluster_content_hash(with_topic) != a


def test_ephemeral_field_does_not_change_hash():
    """Counters / observability fields are NOT routing-critical and must
    not invalidate the cluster's identity from the router's POV."""
    a = compute_cluster_content_hash(_BASE)
    noisy = dict(_BASE)
    noisy["uplift_n"] = 999
    noisy["last_touched_at"] = "2026-06-09T12:00:00Z"
    noisy["evidence_stats"] = {"win": 7, "loss": 1}
    assert compute_cluster_content_hash(noisy) == a


def test_content_hash_itself_excluded():
    """The cluster may already carry an existing content_hash; it must
    not feed back into the hash computation (otherwise hashes would
    become unstable)."""
    a = compute_cluster_content_hash(_BASE)
    self_referential = dict(_BASE)
    self_referential["content_hash"] = "deadbeef" * 8
    assert compute_cluster_content_hash(self_referential) == a


def test_publish_curated_assets_stamps_hashes(tmp_path, monkeypatch):
    """Smoke: the published bridge has content_hash on every curated cluster
    and on any backfilled non-curated cluster."""
    from rosclaw_know import config as _config
    from rosclaw_know import curated_publisher as _cp
    import json

    monkeypatch.setattr(_config, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(_config, "CODE_PATTERNS_DIR", tmp_path / "code_patterns")

    # Seed a non-curated cluster missing content_hash to exercise backfill.
    bridge_path = tmp_path / "bridge_index.json"
    bridge_path.write_text(
        json.dumps({
            "schema_version": "v2",
            "symptom_clusters": {
                "muse_unknown_X": {
                    "standard_name": "synthetic",
                    "domain": "Systems_Compute",
                    "matched_keywords": ["x"],
                    "associated_patterns": ["muse_unknown_X"],
                    "source": None,
                }
            },
        }),
        encoding="utf-8",
    )

    report = _cp.publish_curated_assets()

    data = json.loads(bridge_path.read_text(encoding="utf-8"))
    for cid, c in data["symptom_clusters"].items():
        assert "content_hash" in c, f"{cid} missing content_hash"
        assert len(c["content_hash"]) == 64
        if c.get("source") == "curated":
            assert c["source_tier"] == "S_CURATED_VERIFIED", cid

    assert report["content_hash_backfilled"] >= 1
    assert report["curated_clusters"] >= 1
