"""Tests for the v1 → v2 bridge migration (Sprint 1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import migrate_assets_v1_to_v2 as m  # noqa: E402

from rosclaw_know.schemas import SCHEMA_VERSION, validate_bridge  # noqa: E402


def _make_v1_cluster(
    *, priority: int | None = None, source: str | None = None,
    uplift_mean: float | None = None, uplift_n: int | None = None,
    win_rate: float | None = None,
) -> dict:
    """A minimally valid v1 cluster (one of how's read-only shapes)."""
    return {
        "standard_name": "controller output saturates while integral keeps growing",
        "domain": "Control_Locomotion",
        "matched_keywords": ["pid", "saturation"],
        "cross_domain_analogies": [],
        "associated_patterns": ["anti_windup_pid"],
        **({"priority": priority} if priority is not None else {}),
        **({"source": source} if source is not None else {}),
        **({"uplift_mean": uplift_mean} if uplift_mean is not None else {}),
        **({"uplift_n": uplift_n} if uplift_n is not None else {}),
        **({"win_rate": win_rate} if win_rate is not None else {}),
    }


def _make_v1_bridge(clusters: dict[str, dict] | None = None) -> dict:
    return {
        "symptom_clusters": clusters or {"c1": _make_v1_cluster()},
        "safety_label_index": {"Torque_Overflow": "anti_windup_pid"},
    }


# ── lifecycle inference ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "priority,expected_lifecycle",
    [
        (1, "production"),
        (0, "staging"),
        (-1, "demoted"),
        (None, "needs_validation"),
    ],
)
def test_priority_to_lifecycle(priority, expected_lifecycle) -> None:
    bridge = _make_v1_bridge({"c1": _make_v1_cluster(priority=priority)})
    migrated = m.migrate(bridge)
    assert migrated["symptom_clusters"]["c1"]["metadata"]["lifecycle_status"] == expected_lifecycle


# ── source quality inference ────────────────────────────────────────────


@pytest.mark.parametrize(
    "source,expected_quality",
    [
        ("curated", "S"),
        ("muse", "B"),
        ("autodraft", "D"),
        ("awesome:control-theory", "C"),
        (None, "C"),
        ("something_else", "C"),
    ],
)
def test_source_quality_inference(source, expected_quality) -> None:
    bridge = _make_v1_bridge({"c1": _make_v1_cluster(source=source)})
    migrated = m.migrate(bridge)
    assert migrated["symptom_clusters"]["c1"]["metadata"]["source_quality"] == expected_quality


# ── non-destructive (v1 fields preserved) ───────────────────────────────


def test_v1_fields_preserved() -> None:
    bridge = _make_v1_bridge(
        {"c1": _make_v1_cluster(priority=0, uplift_mean=0.12, uplift_n=8, win_rate=0.75)}
    )
    migrated = m.migrate(bridge)
    c = migrated["symptom_clusters"]["c1"]
    # Every v1 field must be present and unchanged.
    assert c["standard_name"] == bridge["symptom_clusters"]["c1"]["standard_name"]
    assert c["domain"] == "Control_Locomotion"
    assert c["matched_keywords"] == ["pid", "saturation"]
    assert c["associated_patterns"] == ["anti_windup_pid"]
    assert c["priority"] == 0
    assert c["uplift_mean"] == 0.12
    assert c["uplift_n"] == 8
    assert c["win_rate"] == 0.75


def test_evidence_block_built_from_v1_phase4_fields() -> None:
    bridge = _make_v1_bridge(
        {"c1": _make_v1_cluster(uplift_mean=0.18, uplift_n=12, win_rate=0.75)}
    )
    migrated = m.migrate(bridge)
    ev = migrated["symptom_clusters"]["c1"]["metadata"]["evidence"]
    assert ev["n"] == 12
    assert ev["avg_uplift"] == 0.18
    assert ev["win_rate"] == 0.75
    assert ev["hint_use_rate"] == 0.0  # placeholder until Sprint 6
    assert ev["placebo_adjusted_uplift"] is None


# ── idempotency ─────────────────────────────────────────────────────────


def test_idempotent_double_migration() -> None:
    bridge = _make_v1_bridge()
    once = m.migrate(bridge)
    twice = m.migrate(once)
    assert once == twice


def test_idempotent_preserves_hand_edited_task_families() -> None:
    """Operator may hand-edit task_families; second migration must not wipe them."""
    bridge = _make_v1_bridge()
    once = m.migrate(bridge)
    # Pretend operator added some hand-curated task_families.
    once["symptom_clusters"]["c1"]["metadata"]["task_families"] = ["pid_tuning"]
    twice = m.migrate(once)
    assert twice["symptom_clusters"]["c1"]["metadata"]["task_families"] == ["pid_tuning"]


def test_idempotent_preserves_hand_edited_embodiments() -> None:
    bridge = _make_v1_bridge()
    once = m.migrate(bridge)
    once["symptom_clusters"]["c1"]["metadata"]["embodiment_types"] = ["uav"]
    twice = m.migrate(once)
    assert twice["symptom_clusters"]["c1"]["metadata"]["embodiment_types"] == ["uav"]


# ── schema_version stamp ────────────────────────────────────────────────


def test_schema_version_stamped() -> None:
    bridge = _make_v1_bridge()
    migrated = m.migrate(bridge)
    assert migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["symptom_clusters"]["c1"]["metadata"]["schema_version"] == SCHEMA_VERSION


# ── safety_label_index normalization ────────────────────────────────────


def test_safety_label_index_str_normalized_to_list() -> None:
    bridge = _make_v1_bridge()
    bridge["safety_label_index"] = {"Torque_Overflow": "anti_windup_pid"}
    migrated = m.migrate(bridge)
    assert migrated["safety_label_index"] == {"Torque_Overflow": ["anti_windup_pid"]}


def test_safety_label_index_list_unchanged() -> None:
    bridge = _make_v1_bridge()
    bridge["safety_label_index"] = {"Torque_Overflow": ["a", "b"]}
    migrated = m.migrate(bridge)
    assert migrated["safety_label_index"] == {"Torque_Overflow": ["a", "b"]}


# ── migrated tree passes the v2 schema ──────────────────────────────────


def test_migrated_tree_passes_v2_validation() -> None:
    bridge = _make_v1_bridge({
        "c1": _make_v1_cluster(priority=1, source="curated"),
        "c2": _make_v1_cluster(priority=0, source="muse"),
        "c3": _make_v1_cluster(priority=-1),
        "c4": _make_v1_cluster(priority=None),
    })
    migrated = m.migrate(bridge)
    # Must not raise.
    validate_bridge(migrated)


# ── CLI smoke: --check on already-migrated tree exits 0 ────────────────


def test_check_exit_codes(tmp_path: Path) -> None:
    bridge = _make_v1_bridge()
    p = tmp_path / "bridge.json"
    p.write_text(json.dumps(bridge), encoding="utf-8")

    # First run with --check should report DRIFT.
    rc = m.main([str(p), "--check"])
    assert rc == 1

    # Apply.
    rc = m.main([str(p), "--apply"])
    assert rc == 0

    # Now --check should be clean.
    rc = m.main([str(p), "--check"])
    assert rc == 0


def test_apply_writes_atomically_and_loads_back(tmp_path: Path) -> None:
    bridge = _make_v1_bridge()
    p = tmp_path / "bridge.json"
    p.write_text(json.dumps(bridge), encoding="utf-8")
    rc = m.main([str(p), "--apply"])
    assert rc == 0

    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert "metadata" in loaded["symptom_clusters"]["c1"]
    assert loaded["schema_version"] == SCHEMA_VERSION
