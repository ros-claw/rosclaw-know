"""Tests for the YAML curated registry (Sprint 1).

These tests guard the migration path from ``CURATED_SAFETY_PATTERNS`` constants
to ``data/curated_registry/**/*.yaml``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from rosclaw_know import config
from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS
from rosclaw_know.curated_registry import (
    load_curated_patterns,
    load_registry,
    registry_enabled,
)


@pytest.fixture
def registry_entries():
    return load_registry()


class TestRegistryLoader:
    def test_registry_has_all_legacy_patterns(self, registry_entries):
        legacy_ids = {p.pattern_id for p in CURATED_SAFETY_PATTERNS}
        registry_ids = {e.id for e in registry_entries}
        assert registry_ids == legacy_ids

    def test_registry_count_is_15(self, registry_entries):
        assert len(registry_entries) == 15

    def test_every_entry_has_topic_group_and_tag(self, registry_entries):
        for entry in registry_entries:
            assert entry.topic_group, f"{entry.id}: missing topic_group"
            assert entry.topic_tag, f"{entry.id}: missing topic_tag"

    def test_as_curated_pattern_matches_legacy(self, registry_entries):
        legacy_by_id = {p.pattern_id: p for p in CURATED_SAFETY_PATTERNS}
        for entry in registry_entries:
            legacy = legacy_by_id[entry.id]
            pattern = entry.to_curated_pattern()
            assert pattern.pattern_id == legacy.pattern_id
            assert pattern.safety_label == legacy.safety_label
            assert pattern.standard_name == legacy.standard_name
            assert pattern.domain == legacy.domain
            assert set(pattern.matched_keywords) == set(legacy.matched_keywords)
            assert pattern.topic_group == legacy.topic_group
            assert pattern.topic_tag == legacy.topic_tag


class TestLoadCuratedPatternsSwitch:
    def test_default_uses_legacy_constants(self, monkeypatch):
        monkeypatch.delenv("ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED", raising=False)
        assert registry_enabled() is False
        patterns = load_curated_patterns()
        assert len(patterns) == len(CURATED_SAFETY_PATTERNS)
        assert [p.pattern_id for p in patterns] == [p.pattern_id for p in CURATED_SAFETY_PATTERNS]

    def test_enabled_uses_registry(self, monkeypatch):
        monkeypatch.setenv("ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED", "1")
        assert registry_enabled() is True
        patterns = load_curated_patterns()
        assert len(patterns) == 15
        assert {p.pattern_id for p in patterns} == {p.pattern_id for p in CURATED_SAFETY_PATTERNS}


class TestCuratedRegistryEntry:
    def test_a_tier_requires_routing_guard_coverage(self, registry_entries):
        for entry in registry_entries:
            if entry.source_tier in ("S_CURATED_VERIFIED", "A_CURATED_REVIEWED"):
                assert len(entry.routing_guard.positive_queries) >= 1
                assert len(entry.routing_guard.collateral_queries) >= 2

    def test_body_fields_present(self, registry_entries):
        for entry in registry_entries:
            assert entry.body.symptom.strip()
            assert entry.body.diagnosis.strip()
            assert entry.body.fix.strip()
            assert entry.body.anti_pattern.strip()
            assert entry.body.expected_signal.strip()


class TestRegistryValidationScript:
    def test_validate_script_exits_zero(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "validate_curated_registry.py"
        assert script.exists()
        # Run in a subprocess to avoid import side effects.

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


class TestBuildFromRegistryScript:
    def test_build_script_produces_15_clusters_and_patterns(self, tmp_path):
        script = (
            Path(__file__).resolve().parent.parent / "scripts" / "build_curated_from_registry.py"
        )
        assert script.exists()

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        out_dir = tmp_path / "assets"
        result = subprocess.run(
            [sys.executable, str(script), "--out-dir", str(out_dir)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        bridge = out_dir / "bridge_index.json"
        code_patterns = out_dir / "code_patterns"
        assert bridge.exists()
        assert code_patterns.exists()
        import json

        data = json.loads(bridge.read_text(encoding="utf-8"))
        clusters = data.get("symptom_clusters", {})
        assert len(clusters) == 15
        assert len(list(code_patterns.glob("*.md"))) == 15


class TestCompareScript:
    def test_compare_script_reports_equivalent(self, tmp_path):
        build_script = (
            Path(__file__).resolve().parent.parent / "scripts" / "build_curated_from_registry.py"
        )
        compare_script = (
            Path(__file__).resolve().parent.parent / "scripts" / "compare_registry_to_legacy.py"
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = str(config.PROJECT_ROOT / "src")
        out_dir = tmp_path / "assets"
        subprocess.run(
            [sys.executable, str(build_script), "--out-dir", str(out_dir)],
            cwd=config.PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [sys.executable, str(compare_script), "--registry-assets", str(out_dir)],
            cwd=config.PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "semantically equivalent" in result.stdout
