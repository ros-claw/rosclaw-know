"""Tests for Bridge Schema v2 and its validation scripts (Sprint 2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rosclaw_know import config
from rosclaw_know.bridge_schema import (
    BridgeIndexV2,
    compute_content_hash,
    compute_metadata_hash,
    validate_bridge_index,
)


class TestBridgeIndexV2Model:
    @pytest.mark.parametrize("version", [2, "2", "2.0", "v2"])
    def test_schema_version_normalizes_to_canonical_2_0(self, version):
        bi = BridgeIndexV2(
            schema_version=version,
            symptom_clusters={},
        )
        assert bi.schema_version == "2.0"

    def test_invalid_schema_version_rejected(self):
        with pytest.raises(ValueError):
            BridgeIndexV2(schema_version="1.0", symptom_clusters={})


def _make_v2_cluster(**overrides: object) -> dict:
    cluster = {
        "standard_name": "Test symptom for bridge schema v2",
        "domain": "Control_Locomotion",
        "robot_type": "manipulator",
        "topic_group": "control-loop-stability",
        "topic_tag": "test-tag",
        "source": "curated",
        "source_tier": "A_CURATED_REVIEWED",
        "status": "active",
        "runtime_eligible": True,
        "priority": 1,
        "matched_keywords": ["pid", "oscillation"],
        "associated_patterns": ["test_pattern"],
        "routing_guard": {
            "positive_queries": ["T_001"],
            "collateral_queries": ["T_W_005", "T_W_007"],
            "adversarial_queries": [],
            "negative_signatures": [],
            "saturation_signatures": [],
        },
        "evidence": {
            "retrieval_status": "passed",
            "llm_judge_status": "unstable",
            "official_verifier_status": "not_started",
            "last_verified_panel": "iter_test",
            "notes": [],
        },
        "demotion": {"demote_reason": None, "confidence_score": None},
    }
    cluster.update(overrides)
    cluster["content_hash"] = compute_content_hash(cluster)
    cluster["metadata_hash"] = compute_metadata_hash(cluster)
    return cluster


class TestContentAndMetadataHash:
    def test_content_hash_rotates_when_routing_critical_field_changes(self):
        c1 = _make_v2_cluster()
        c2 = _make_v2_cluster(standard_name="Changed symptom description")
        c2["content_hash"] = compute_content_hash(c2)
        c2["metadata_hash"] = compute_metadata_hash(c2)
        assert c1["content_hash"] != c2["content_hash"]

    def test_content_hash_stable_when_evidence_changes(self):
        c1 = _make_v2_cluster()
        c2 = _make_v2_cluster(
            evidence={
                "retrieval_status": "passed",
                "llm_judge_status": "passed",  # changed
                "official_verifier_status": "not_started",
                "last_verified_panel": "iter_test",
                "notes": [],
            }
        )
        c2["content_hash"] = compute_content_hash(c2)
        assert c1["content_hash"] == c2["content_hash"]

    def test_metadata_hash_rotates_when_evidence_changes(self):
        c1 = _make_v2_cluster()
        c2 = _make_v2_cluster(
            evidence={
                "retrieval_status": "passed",
                "llm_judge_status": "passed",
                "official_verifier_status": "not_started",
                "last_verified_panel": "iter_test",
                "notes": [],
            }
        )
        c2["content_hash"] = compute_content_hash(c2)
        c2["metadata_hash"] = compute_metadata_hash(c2)
        assert c1["metadata_hash"] != c2["metadata_hash"]


class TestValidateBridgeIndex:
    def test_valid_v2_bridge_passes(self):
        data = {
            "schema_version": 2,
            "symptom_clusters": {"test_pattern": _make_v2_cluster()},
        }
        report = validate_bridge_index(data)
        assert report["ok"] is True
        assert not report["errors"]

    def test_schema_version_string_2_0_passes(self):
        data = {
            "schema_version": "2.0",
            "symptom_clusters": {"test_pattern": _make_v2_cluster()},
        }
        report = validate_bridge_index(data)
        assert report["ok"] is True
        assert not report["errors"]

    def test_schema_version_v2_passes(self):
        data = {
            "schema_version": "v2",
            "symptom_clusters": {"test_pattern": _make_v2_cluster()},
        }
        report = validate_bridge_index(data)
        assert report["ok"] is True
        assert not report["errors"]

    def test_schema_version_string_2_passes(self):
        data = {
            "schema_version": "2",
            "symptom_clusters": {"test_pattern": _make_v2_cluster()},
        }
        report = validate_bridge_index(data)
        assert report["ok"] is True
        assert not report["errors"]

    def test_missing_schema_version_fails(self):
        data = {"symptom_clusters": {"test_pattern": _make_v2_cluster()}}
        report = validate_bridge_index(data)
        assert report["ok"] is False
        assert any("schema_version" in e for e in report["errors"])

    def test_curated_missing_topic_field_fails(self):
        cluster = _make_v2_cluster(topic_tag="")
        data = {"schema_version": 2, "symptom_clusters": {"test_pattern": cluster}}
        report = validate_bridge_index(data)
        assert report["ok"] is False
        assert any("topic_group or topic_tag" in e for e in report["errors"])

    def test_content_hash_mismatch_fails(self):
        cluster = _make_v2_cluster()
        cluster["content_hash"] = "0" * 64
        data = {"schema_version": 2, "symptom_clusters": {"test_pattern": cluster}}
        report = validate_bridge_index(data)
        assert report["ok"] is False
        assert any("content_hash mismatch" in e for e in report["errors"])

    def test_legacy_v1_cluster_skipped(self):
        cluster = {
            "standard_name": "Legacy synth cluster",
            "domain": "Planning_Decision",
            "matched_keywords": ["a", "b"],
            "associated_patterns": ["legacy_pattern"],
            "source_tier": "C_MUSE_SYNTH",
            "content_hash": "abcd1234",
        }
        data = {"schema_version": 2, "symptom_clusters": {"legacy": cluster}}
        report = validate_bridge_index(data)
        assert report["ok"] is True
        assert not report["errors"]

    def test_f_demoted_requires_demote_reason(self):
        cluster = _make_v2_cluster(
            source_tier="F_DEMOTED",
            status="demoted",
            runtime_eligible=False,
            priority=-1,
            demotion={"demote_reason": None, "confidence_score": 0.5},
        )
        data = {"schema_version": 2, "symptom_clusters": {"demoted": cluster}}
        report = validate_bridge_index(data)
        assert report["ok"] is False
        assert any("demotion.demote_reason" in e for e in report["errors"])


class TestValidationScripts:
    def test_validate_bridge_schema_script_exits_zero(self, tmp_path):
        script = Path(__file__).resolve().parent.parent / "scripts" / "validate_bridge_schema.py"
        bridge = tmp_path / "bridge_index.json"
        data = {
            "schema_version": 2,
            "symptom_clusters": {"test_pattern": _make_v2_cluster()},
        }
        bridge.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(script), "--bridge", str(bridge)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["ok"] is True

    def test_validate_topic_coverage_script_exits_zero(self, tmp_path):
        script = Path(__file__).resolve().parent.parent / "scripts" / "validate_topic_coverage.py"
        bridge = tmp_path / "bridge_index.json"
        data = {
            "schema_version": 2,
            "symptom_clusters": {
                "curated_ok": _make_v2_cluster(),
                "synth_no_tag": {
                    "standard_name": "Synth without topic tag",
                    "source": "muse",
                    "source_tier": "C_MUSE_SYNTH",
                },
            },
        }
        bridge.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(script), "--bridge", str(bridge)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["coverage"] == "1/1"

    def test_validate_topic_coverage_script_fails_when_curated_missing_tag(self, tmp_path):
        script = Path(__file__).resolve().parent.parent / "scripts" / "validate_topic_coverage.py"
        bridge = tmp_path / "bridge_index.json"
        data = {
            "schema_version": 2,
            "symptom_clusters": {"curated_bad": _make_v2_cluster(topic_tag="")},
        }
        bridge.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(script), "--bridge", str(bridge)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        report = json.loads(result.stdout)
        assert report["coverage"] == "0/1"


class TestInspectBridgeDiff:
    def test_detects_added_removed_and_unchanged(self, tmp_path):
        script = Path(__file__).resolve().parent.parent / "scripts" / "inspect_bridge_diff.py"
        before = tmp_path / "before.json"
        after = tmp_path / "after.json"
        c1 = _make_v2_cluster(standard_name="Original")
        c2 = _make_v2_cluster(standard_name="Original")
        c3 = _make_v2_cluster(standard_name="Changed")
        c3["content_hash"] = compute_content_hash(c3)
        c3["metadata_hash"] = compute_metadata_hash(c3)

        before.write_text(
            json.dumps(
                {"schema_version": 2, "symptom_clusters": {"keep": c1, "gone": c2}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        after.write_text(
            json.dumps(
                {"schema_version": 2, "symptom_clusters": {"keep": c1, "new": c3}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(script), "--before", str(before), "--after", str(after)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["added"] == ["new"]
        assert report["removed"] == ["gone"]
        assert report["unchanged"] == ["keep"]
        assert report["changed_count"] == 0
