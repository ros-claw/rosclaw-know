"""Tests for Bridge Schema v2 content_hash semantics.

See docs/know-how下一步建议06-13.md §6.3 — same routing-critical inputs must
produce the same hash; any routing-critical change must produce a new hash;
ephemeral/governance fields must NOT affect the content hash.
"""
from __future__ import annotations

from rosclaw_know.bridge_schema import ROUTING_CRITICAL_FIELDS, compute_content_hash

_BASE = {
    "standard_name": "PID integral wind-up drives actuator into torque saturation",
    "domain": "Control_Locomotion",
    "robot_type": "actuator",
    "topic_group": "control-loop-stability",
    "topic_tag": "pid-integral-windup",
    "source": "curated",
    "source_tier": "A_CURATED_REVIEWED",
    "status": "active",
    "runtime_eligible": True,
    "priority": 1,
    "matched_keywords": ["pid", "torque", "windup"],
    "associated_patterns": ["anti_windup_pid"],
}


def test_routing_critical_fields_match_v2_contract():
    """Each routing-critical field from §6.3 must be present in the tuple."""
    expected = {
        "standard_name",
        "domain",
        "robot_type",
        "topic_group",
        "topic_tag",
        "matched_keywords",
        "associated_patterns",
        "source",
        "source_tier",
        "status",
        "runtime_eligible",
        "priority",
    }
    assert expected == set(ROUTING_CRITICAL_FIELDS)


def test_same_input_same_hash():
    a = compute_content_hash(dict(_BASE))
    b = compute_content_hash(dict(_BASE))
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_list_order_does_not_affect_hash():
    a = compute_content_hash(_BASE)
    shuffled = dict(_BASE)
    shuffled["matched_keywords"] = ["windup", "pid", "torque"]
    b = compute_content_hash(shuffled)
    assert a == b


def test_routing_critical_change_changes_hash():
    a = compute_content_hash(_BASE)

    cases = [
        ("standard_name", "different name"),
        ("domain", "Systems_Compute"),
        ("robot_type", "uav"),
        ("topic_group", "other-group"),
        ("topic_tag", "other-tag"),
        ("source", "muse"),
        ("source_tier", "C_MUSE_SYNTH"),
        ("status", "demoted"),
        ("runtime_eligible", False),
        ("priority", -1),
        ("matched_keywords", ["unrelated"]),
        ("associated_patterns", ["other_pattern"]),
    ]
    for field, new_value in cases:
        mutated = dict(_BASE)
        mutated[field] = new_value
        assert compute_content_hash(mutated) != a, f"changing {field} must change content_hash"


def test_adding_routing_critical_field_changes_hash():
    """Adding a routing-critical field where there wasn't one before
    must produce a different hash (the absent-vs-present distinction
    matters for delta-path correctness on reload)."""
    minimal = {k: v for k, v in _BASE.items() if k != "robot_type"}
    a = compute_content_hash(minimal)
    with_robot = dict(minimal)
    with_robot["robot_type"] = "manipulator"
    assert compute_content_hash(with_robot) != a


def test_ephemeral_field_does_not_change_hash():
    """Counters / observability / governance fields are NOT routing-critical
    and must not invalidate the cluster's identity from the router's POV."""
    a = compute_content_hash(_BASE)
    noisy = dict(_BASE)
    noisy["uplift_n"] = 999
    noisy["last_touched_at"] = "2026-06-09T12:00:00Z"
    noisy["evidence_stats"] = {"win": 7, "loss": 1}
    noisy["routing_guard"] = {"positive_queries": ["T_001"]}
    assert compute_content_hash(noisy) == a


def test_content_hash_itself_excluded():
    """The cluster may already carry an existing content_hash; it must
    not feed back into the hash computation (otherwise hashes would
    become unstable)."""
    a = compute_content_hash(_BASE)
    self_referential = dict(_BASE)
    self_referential["content_hash"] = "deadbeef" * 8
    self_referential["metadata_hash"] = "cafebabe" * 8
    assert compute_content_hash(self_referential) == a


def test_publish_curated_assets_stamps_hashes(tmp_path, monkeypatch):
    """Smoke: the published bridge has content_hash + metadata_hash on every
    curated cluster and on any backfilled non-curated cluster."""
    import json

    from rosclaw_know import config as _config
    from rosclaw_know import curated_publisher as _cp

    monkeypatch.setattr(_config, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(_config, "CODE_PATTERNS_DIR", tmp_path / "code_patterns")

    # Seed a non-curated cluster missing content_hash to exercise backfill.
    bridge_path = tmp_path / "bridge_index.json"
    bridge_path.write_text(
        json.dumps({
            "schema_version": 2,
            "symptom_clusters": {
                "muse_unknown_X": {
                    "standard_name": "synthetic",
                    "domain": "Systems_Compute",
                    "matched_keywords": ["x"],
                    "associated_patterns": ["muse_unknown_X"],
                }
            },
        }),
        encoding="utf-8",
    )

    report = _cp.publish_curated_assets()

    data = json.loads(bridge_path.read_text(encoding="utf-8"))
    for cid, c in data["symptom_clusters"].items():
        assert "content_hash" in c, f"{cid} missing content_hash"
        assert "metadata_hash" in c, f"{cid} missing metadata_hash"
        assert len(c["content_hash"]) == 64
        if c.get("source") == "curated":
            assert c["source_tier"] in ("A_CURATED_REVIEWED", "S_CURATED_VERIFIED"), cid

    assert report["content_hash_backfilled"] >= 1
    assert report["curated_clusters"] >= 1
