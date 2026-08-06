"""Tests for the frozen-bundle freezer (scripts/freeze_bundle.py).

Sprint 4 bundle layout per know-how下一步建议06-13.md §8:
  bridge_index.json + code_patterns/
  + routing_panel.yaml + routing_panel_result.json + routing_panel_result.md
  + healthz_snapshot.json + policy_config.yaml
  + know_sha.txt + how_sha.txt
  + bundle_manifest.json + sha256sum.txt

Tests cover: git head capture, sha256 stability, deterministic file ordering
in _walk_bundle, health gating, and end-to-end freeze on a fake assets/ tree.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "freeze_bundle",
        Path(__file__).resolve().parent.parent / "scripts" / "freeze_bundle.py",
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def fb():
    return _load_module()


def _fake_health():
    return {
        "status": "ok",
        "router_backend": "seekdb",
        "assets_loaded": True,
        "similarity_floor": 0.5,
        "tier_aware_ranking": False,
        "tier_tiebreak_eps": 0.005,
    }


def _fake_panel(tmp_path: Path) -> Path:
    panel = tmp_path / "routing_panel.yaml"
    panel.write_text(
        "schema_version: 2\npanel_id: test\ntasks:\n"
        "  - task_id: TASK_001\n"
        "    type: positive\n"
        "    query: test query one\n"
        "    expected_pattern_any: [p1]\n",
        encoding="utf-8",
    )
    return panel


class TestGitHead:
    def test_captures_sha_and_branch(self, fb, tmp_path):
        repo = tmp_path / "fake_repo"
        repo.mkdir()
        subprocess.check_call(["git", "-C", str(repo), "init", "-q", "-b", "main"])
        subprocess.check_call(
            ["git", "-C", str(repo), "config", "user.email", "x@y.z"]
        )
        subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "X"])
        (repo / "f.txt").write_text("hi", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(repo), "add", "f.txt"])
        subprocess.check_call(
            ["git", "-C", str(repo), "commit", "-qm", "first"]
        )

        head = fb._git_head(repo)
        assert "sha" in head and len(head["sha"]) == 40
        assert head["short_sha"] == head["sha"][:8]
        assert head["branch"] in ("main", "master")
        assert head["dirty"] is False
        assert head["porcelain"] == ""

    def test_detects_dirty(self, fb, tmp_path):
        repo = tmp_path / "dirty_repo"
        repo.mkdir()
        subprocess.check_call(["git", "-C", str(repo), "init", "-q", "-b", "main"])
        subprocess.check_call(
            ["git", "-C", str(repo), "config", "user.email", "x@y.z"]
        )
        subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "X"])
        (repo / "a.txt").write_text("a", encoding="utf-8")
        subprocess.check_call(["git", "-C", str(repo), "add", "a.txt"])
        subprocess.check_call(["git", "-C", str(repo), "commit", "-qm", "x"])
        (repo / "a.txt").write_text("a-changed", encoding="utf-8")

        head = fb._git_head(repo)
        assert head["dirty"] is True
        assert "a.txt" in head["porcelain"]

    def test_non_git_path_returns_error(self, fb, tmp_path):
        head = fb._git_head(tmp_path / "no_repo_here")
        assert "error" in head


class TestSha256:
    def test_stable_and_distinguishes(self, fb, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"X" * 1024)
        b.write_bytes(b"Y" * 1024)
        assert fb._sha256_file(a) == fb._sha256_file(a)
        assert fb._sha256_file(a) != fb._sha256_file(b)

    def test_large_file_chunked(self, fb, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"A" * (65536 * 3 + 17))
        h = fb._sha256_file(f)
        assert len(h) == 64
        g = tmp_path / "big2.bin"
        g.write_bytes(b"A" * (65536 * 3 + 17))
        assert fb._sha256_file(g) == h


class TestWalkBundle:
    def test_excludes_sha256sum(self, fb, tmp_path):
        bundle = tmp_path / "b"
        bundle.mkdir()
        (bundle / "a.txt").write_text("a", encoding="utf-8")
        (bundle / "b.txt").write_text("b", encoding="utf-8")
        (bundle / "sha256sum.txt").write_text("ignored", encoding="utf-8")
        sub = bundle / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("c", encoding="utf-8")

        rels = sorted(str(f.relative_to(bundle)) for f in fb._walk_bundle(bundle))
        assert "a.txt" in rels
        assert "b.txt" in rels
        assert "sub/c.txt" in rels
        assert "sha256sum.txt" not in rels

    def test_deterministic_order(self, fb, tmp_path):
        bundle = tmp_path / "b"
        bundle.mkdir()
        for name in ["z.txt", "a.txt", "m.txt"]:
            (bundle / name).write_text("x", encoding="utf-8")

        order_1 = [str(f.relative_to(bundle)) for f in fb._walk_bundle(bundle)]
        order_2 = [str(f.relative_to(bundle)) for f in fb._walk_bundle(bundle)]
        assert order_1 == order_2
        assert order_1 == sorted(order_1)


def _fake_git_head(repo: Path) -> dict:
    return {
        "repo": str(repo),
        "sha": "a" * 40,
        "short_sha": "a" * 8,
        "branch": "main",
        "dirty": False,
        "porcelain": "",
    }


class TestFreezeEndToEnd:
    def _fake_assets(self, base: Path) -> Path:
        """Build a minimal fake assets/ tree the freezer can walk."""
        assets = base / "data" / "assets"
        assets.mkdir(parents=True)
        bridge = {
            "schema_version": 2,
            "symptom_clusters": {
                "p1": {"source": "curated", "content_hash": "h1"},
                "p2": {"source_tier": "C_MUSE_SYNTH", "content_hash": "h2"},
            },
        }
        (assets / "bridge_index.json").write_text(
            json.dumps(bridge), encoding="utf-8"
        )
        patterns = assets / "code_patterns"
        patterns.mkdir()
        (patterns / "p1.md").write_text("# p1", encoding="utf-8")
        return assets

    def _patch_freeze(self, fb, tmp_path, monkeypatch):
        assets = self._fake_assets(tmp_path)
        monkeypatch.setattr(fb.config, "ASSETS_DIR", assets)
        monkeypatch.setattr(fb.config, "CODE_PATTERNS_DIR", assets / "code_patterns")
        monkeypatch.setattr(fb.config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(fb.config, "DATA_DIR", tmp_path / "data")
        frozen_root = tmp_path / "data" / "frozen"
        monkeypatch.setattr(fb, "FROZEN_ROOT", frozen_root)
        monkeypatch.setattr(fb, "_git_head", _fake_git_head)
        monkeypatch.setattr(fb, "_fetch_health", lambda base, timeout=5: _fake_health())
        def _fake_run_panel(*, out_json, out_markdown, **kwargs):
            report = {"summary": {"total": 2, "pass": 2, "fail": 0}, "results": []}
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(report), encoding="utf-8")
            out_markdown.write_text("# Panel\n\nALL PASS\n", encoding="utf-8")
            return report

        monkeypatch.setattr(fb, "_run_panel", _fake_run_panel)
        return frozen_root

    def test_freeze_produces_manifest_and_sha(self, fb, tmp_path, monkeypatch):
        frozen_root = self._patch_freeze(fb, tmp_path, monkeypatch)
        panel = _fake_panel(tmp_path)
        fake_how = tmp_path / "fake_how"
        fake_how.mkdir()

        manifest = fb.freeze(
            label="test_label",
            how_base="http://127.0.0.1:8088",
            how_root=fake_how,
            panel=panel,
            api_key="test-key",
            policy_config=None,
            notes="unit test",
            force=False,
        )
        bundle = frozen_root / "test_label"
        assert bundle.exists()
        for f in [
            "bridge_index.json",
            "know_sha.txt",
            "how_sha.txt",
            "policy_config.yaml",
            "healthz_snapshot.json",
            "routing_panel.yaml",
            "routing_panel_result.json",
            "routing_panel_result.md",
            "bundle_manifest.json",
            "sha256sum.txt",
        ]:
            assert (bundle / f).exists(), f"missing {f}"
        assert (bundle / "code_patterns" / "p1.md").exists()

        assert manifest["label"] == "test_label"
        assert manifest["cluster_count"] == 2
        assert manifest["curated_count"] == 1
        assert manifest["clusters_with_content_hash"] == 2
        assert manifest["panel_pass"] == 2
        assert manifest["panel_total"] == 2
        assert manifest["schema_version"] == 2

        sha_text = (bundle / "sha256sum.txt").read_text(encoding="utf-8").strip()
        for line in sha_text.splitlines():
            recorded, rel = line.split("  ", 1)
            real = fb._sha256_file(bundle / rel)
            assert recorded == real, f"sha mismatch for {rel}"

    def test_freeze_refuses_overwrite_without_force(self, fb, tmp_path, monkeypatch):
        self._patch_freeze(fb, tmp_path, monkeypatch)
        panel = _fake_panel(tmp_path)

        fb.freeze(
            label="existing",
            how_base="http://127.0.0.1:8088",
            how_root=tmp_path,
            panel=panel,
            api_key="test-key",
            policy_config=None,
            notes="",
            force=False,
        )
        with pytest.raises(SystemExit):
            fb.freeze(
                label="existing",
                how_base="http://127.0.0.1:8088",
                how_root=tmp_path,
                panel=panel,
                api_key="test-key",
                policy_config=None,
                notes="",
                force=False,
            )

    def test_freeze_force_overwrites(self, fb, tmp_path, monkeypatch):
        frozen_root = self._patch_freeze(fb, tmp_path, monkeypatch)
        panel = _fake_panel(tmp_path)

        fb.freeze(
            label="rw",
            how_base="http://127.0.0.1:8088",
            how_root=tmp_path,
            panel=panel,
            api_key="test-key",
            policy_config=None,
            notes="first",
            force=False,
        )
        m1 = json.loads(
            (frozen_root / "rw" / "bundle_manifest.json").read_text(encoding="utf-8")
        )

        fb.freeze(
            label="rw",
            how_base="http://127.0.0.1:8088",
            how_root=tmp_path,
            panel=panel,
            api_key="test-key",
            policy_config=None,
            notes="second",
            force=True,
        )
        m2 = json.loads(
            (frozen_root / "rw" / "bundle_manifest.json").read_text(encoding="utf-8")
        )
        assert m1["notes"] == "first"
        assert m2["notes"] == "second"

    def test_freeze_refuses_degraded_how(self, fb, tmp_path, monkeypatch):
        self._patch_freeze(fb, tmp_path, monkeypatch)
        panel = _fake_panel(tmp_path)
        monkeypatch.setattr(
            fb,
            "_fetch_health",
            lambda base, timeout=5: {
                "status": "degraded",
                "router_backend": "inmemory",
                "assets_loaded": True,
            },
        )

        with pytest.raises(SystemExit):
            fb.freeze(
                label="degraded",
                how_base="http://127.0.0.1:8088",
                how_root=tmp_path,
                panel=panel,
                api_key="test-key",
                policy_config=None,
                notes="",
                force=False,
            )

    def test_freeze_refuses_panel_failures(self, fb, tmp_path, monkeypatch):
        self._patch_freeze(fb, tmp_path, monkeypatch)
        panel = _fake_panel(tmp_path)
        monkeypatch.setattr(
            fb,
            "_run_panel",
            lambda **kwargs: {
                "summary": {"total": 2, "pass": 1, "fail": 1},
                "results": [],
            },
        )

        with pytest.raises(SystemExit):
            fb.freeze(
                label="panel_fail",
                how_base="http://127.0.0.1:8088",
                how_root=tmp_path,
                panel=panel,
                api_key="test-key",
                policy_config=None,
                notes="",
                force=False,
            )
