"""Tests for the frozen-bundle freezer (scripts/freeze_bundle.py).

Bundle layout per docs/know-how下一步建议.md §4.2:
  bridge_index.json + code_patterns/ + routing_canary.json
  + know_commit.txt + how_commit.txt + eval_panel.yaml
  + model_config.yaml + bundle_manifest.json + sha256sum.txt

Tests cover: git head capture, frontier panel regex (case where task IDs
have lowercase suffixes), model config redaction, sha256 stability,
deterministic file ordering in _walk_bundle, end-to-end freeze on a
fake assets/ tree.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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


class TestGitHead:
    def test_captures_sha_and_branch(self, fb, tmp_path):
        # Build a fake git repo with one commit
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
        # Now leave an unstaged change
        (repo / "a.txt").write_text("a-changed", encoding="utf-8")

        head = fb._git_head(repo)
        assert head["dirty"] is True
        assert "a.txt" in head["porcelain"]

    def test_non_git_path_returns_error(self, fb, tmp_path):
        head = fb._git_head(tmp_path / "no_repo_here")
        assert "error" in head


class TestFrontierPanel:
    def test_extracts_home_and_wild_task_ids(self, fb, tmp_path):
        # Fake verify_frontier_eng.py with the exact regex shape we use
        fake_verify = tmp_path / "scripts" / "verify_frontier_eng.py"
        fake_verify.parent.mkdir()
        fake_verify.write_text(
            'tasks = [\n'
            '    {"task_id": "TASK_001_PIDTuning"},\n'
            '    {"task_id": "TASK_W_002_GradExplosionRL"},\n'
            '    {"task_id": "TASK_006_FlashAttention"},\n'
            '    {"task_id": "TASK_W_008_AttentionMemoryOOM"},\n'
            ']',
            encoding="utf-8",
        )
        ids = fb._snapshot_frontier_panel(tmp_path)
        assert "TASK_001_PIDTuning" in ids
        assert "TASK_W_002_GradExplosionRL" in ids
        assert "TASK_006_FlashAttention" in ids
        assert "TASK_W_008_AttentionMemoryOOM" in ids

    def test_lowercase_suffix_captured(self, fb, tmp_path):
        # Original regex was [A-Z0-9_]+ which dropped lowercase tails — verify
        # the fix [A-Za-z0-9_]+ catches them.
        fake_verify = tmp_path / "scripts" / "verify_frontier_eng.py"
        fake_verify.parent.mkdir()
        fake_verify.write_text(
            '[{"task_id": "TASK_007_idTuning"}]', encoding="utf-8"
        )
        assert "TASK_007_idTuning" in fb._snapshot_frontier_panel(tmp_path)

    def test_missing_verify_returns_empty(self, fb, tmp_path):
        assert fb._snapshot_frontier_panel(tmp_path) == []


class TestModelConfig:
    def test_no_api_key_leaked(self, fb):
        cfg = fb._model_config()
        # Just an absolute structural assertion — no API key, just metadata.
        s = json.dumps(cfg)
        assert "DEEPSEEK_API_KEY" not in s
        assert "api_key" not in s.lower()
        assert "deepseek_base_url" in cfg
        assert "deepseek_muse_model" in cfg


class TestSha256:
    def test_stable_and_distinguishes(self, fb, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"X" * 1024)
        b.write_bytes(b"Y" * 1024)
        assert fb._sha256_file(a) == fb._sha256_file(a)
        assert fb._sha256_file(a) != fb._sha256_file(b)

    def test_large_file_chunked(self, fb, tmp_path):
        # _sha256_file reads in 64KB chunks; pump a file > 1 chunk.
        f = tmp_path / "big.bin"
        f.write_bytes(b"A" * (65536 * 3 + 17))
        h = fb._sha256_file(f)
        assert len(h) == 64
        # Identical content elsewhere → identical hash
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
        # Create files in non-alphabetical order
        for name in ["z.txt", "a.txt", "m.txt"]:
            (bundle / name).write_text("x", encoding="utf-8")

        order_1 = [str(f.relative_to(bundle)) for f in fb._walk_bundle(bundle)]
        order_2 = [str(f.relative_to(bundle)) for f in fb._walk_bundle(bundle)]
        assert order_1 == order_2
        assert order_1 == sorted(order_1)


class TestFreezeEndToEnd:
    def _fake_assets(self, base: Path) -> Path:
        """Build a minimal fake assets/ tree the freezer can walk."""
        assets = base / "data" / "assets"
        assets.mkdir(parents=True)
        bridge = {
            "schema_version": "v2",
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
        canary = {"schema_version": 1, "canaries": [{"name": "p1"}]}
        (assets / "routing_canary.json").write_text(
            json.dumps(canary), encoding="utf-8"
        )
        return assets

    def _fake_verify(self, base: Path) -> None:
        scripts = base / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "verify_frontier_eng.py").write_text(
            'tasks = [{"task_id": "TASK_001"}, {"task_id": "TASK_W_001"}]',
            encoding="utf-8",
        )

    def test_freeze_produces_manifest_and_sha(self, fb, tmp_path, monkeypatch):
        # Stub config so freezer points at our fake tree.
        assets = self._fake_assets(tmp_path)
        self._fake_verify(tmp_path)
        monkeypatch.setattr(fb.config, "ASSETS_DIR", assets)
        monkeypatch.setattr(fb.config, "CODE_PATTERNS_DIR", assets / "code_patterns")
        monkeypatch.setattr(fb.config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(fb.config, "DATA_DIR", tmp_path / "data")
        frozen_root = tmp_path / "data" / "frozen"
        monkeypatch.setattr(fb, "FROZEN_ROOT", frozen_root)
        # Stub a fake how_root so _git_head returns an error (no real repo there)
        fake_how = tmp_path / "fake_how_root"

        manifest = fb.freeze(
            label="test_label", how_root=fake_how, notes="unit test", force=False
        )
        bundle = frozen_root / "test_label"
        assert bundle.exists()
        # All expected files present
        for f in [
            "bridge_index.json",
            "routing_canary.json",
            "know_commit.txt",
            "how_commit.txt",
            "eval_panel.yaml",
            "model_config.yaml",
            "bundle_manifest.json",
            "sha256sum.txt",
        ]:
            assert (bundle / f).exists(), f"missing {f}"
        assert (bundle / "code_patterns" / "p1.md").exists()

        # Manifest content
        assert manifest["label"] == "test_label"
        assert manifest["cluster_count"] == 2
        assert manifest["curated_count"] == 1
        assert manifest["clusters_with_content_hash"] == 2
        assert manifest["panel_home_count"] == 1  # TASK_001
        assert manifest["panel_wild_count"] == 1  # TASK_W_001

        # sha256sum.txt actually verifies against the bundle's files
        sha_text = (bundle / "sha256sum.txt").read_text(encoding="utf-8").strip()
        for line in sha_text.splitlines():
            recorded, rel = line.split("  ", 1)
            real = fb._sha256_file(bundle / rel)
            assert recorded == real, f"sha mismatch for {rel}"

    def test_freeze_refuses_overwrite_without_force(self, fb, tmp_path, monkeypatch):
        assets = self._fake_assets(tmp_path)
        self._fake_verify(tmp_path)
        monkeypatch.setattr(fb.config, "ASSETS_DIR", assets)
        monkeypatch.setattr(fb.config, "CODE_PATTERNS_DIR", assets / "code_patterns")
        monkeypatch.setattr(fb.config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(fb.config, "DATA_DIR", tmp_path / "data")
        frozen_root = tmp_path / "data" / "frozen"
        monkeypatch.setattr(fb, "FROZEN_ROOT", frozen_root)

        fb.freeze(label="existing", how_root=tmp_path, notes="", force=False)
        with pytest.raises(SystemExit):
            fb.freeze(label="existing", how_root=tmp_path, notes="", force=False)

    def test_freeze_force_overwrites(self, fb, tmp_path, monkeypatch):
        assets = self._fake_assets(tmp_path)
        self._fake_verify(tmp_path)
        monkeypatch.setattr(fb.config, "ASSETS_DIR", assets)
        monkeypatch.setattr(fb.config, "CODE_PATTERNS_DIR", assets / "code_patterns")
        monkeypatch.setattr(fb.config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(fb.config, "DATA_DIR", tmp_path / "data")
        frozen_root = tmp_path / "data" / "frozen"
        monkeypatch.setattr(fb, "FROZEN_ROOT", frozen_root)

        fb.freeze(label="rw", how_root=tmp_path, notes="first", force=False)
        m1 = json.loads((frozen_root / "rw" / "bundle_manifest.json").read_text(encoding="utf-8"))

        fb.freeze(label="rw", how_root=tmp_path, notes="second", force=True)
        m2 = json.loads((frozen_root / "rw" / "bundle_manifest.json").read_text(encoding="utf-8"))
        assert m1["notes"] == "first"
        assert m2["notes"] == "second"
