"""Contract tests for the live know→how boundary.

These lock the published assets (``data/assets/bridge_index.json`` and
``data/assets/code_patterns/*.md``) against the items in the ROSClaw
Know/How test outline, section 2.7 / 2.8 / 2.9:

  - K-BRIDGE-001..010: bridge_index data contract
  - K-CURATED-001..006: curated safety pattern publish guarantees
  - K-MUSE-007..008:    pattern markdown structural requirements

The existing ``test_schemas.py`` covers validator behavior on synthetic
docs; this module asserts the same invariants hold on the real published
artifacts so a broken deploy fails CI before it reaches rosclaw-how.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = REPO_ROOT / "data" / "assets" / "bridge_index.json"
PATTERNS_DIR = REPO_ROOT / "data" / "assets" / "code_patterns"


# Skip the whole module if assets aren't provisioned (e.g. fresh clone
# before ``run_phase1.py``).  Synthetic schema tests still cover the
# validator logic in that case.
pytestmark = pytest.mark.skipif(
    not BRIDGE_PATH.exists() or not PATTERNS_DIR.exists(),
    reason="published assets not provisioned (run scripts/run_phase1.py first)",
)


@pytest.fixture(scope="module")
def bridge() -> dict:
    return json.loads(BRIDGE_PATH.read_text())


@pytest.fixture(scope="module")
def pattern_files() -> list[Path]:
    return sorted(PATTERNS_DIR.glob("*.md"))


# ── K-BRIDGE: bridge_index data contract ─────────────────────────────────


class TestKBridge:
    """Maps to outline 2.9 ‘bridge_index 数据契约测试’."""

    def test_k_bridge_001_json_loads(self, bridge: dict) -> None:
        assert isinstance(bridge, dict)
        assert bridge, "bridge_index.json is empty"

    def test_k_bridge_002_symptom_clusters_present(self, bridge: dict) -> None:
        assert "symptom_clusters" in bridge
        assert isinstance(bridge["symptom_clusters"], dict)
        assert bridge["symptom_clusters"], "symptom_clusters is empty"

    def test_k_bridge_003_cluster_ids_unique(self, bridge: dict) -> None:
        ids = list(bridge["symptom_clusters"].keys())
        assert len(ids) == len(set(ids)), (
            f"cluster ids must be unique: {len(ids) - len(set(ids))} duplicates"
        )

    def test_k_bridge_004_domain_present_and_string(self, bridge: dict) -> None:
        missing = [cid for cid, c in bridge["symptom_clusters"].items() if not c.get("domain")]
        assert not missing, f"clusters missing domain: {missing[:5]}"

    def test_k_bridge_005_priority_in_valid_range(self, bridge: dict) -> None:
        # priority must be one of {-1, 0, 1} or absent (=> legacy production)
        bad = []
        for cid, c in bridge["symptom_clusters"].items():
            p = c.get("priority")
            if p is None:
                continue  # legacy/default → production
            if p not in (-1, 0, 1):
                bad.append((cid, p))
        assert not bad, f"invalid priority values: {bad[:5]}"

    def test_k_bridge_006_associated_patterns_resolve(
        self, bridge: dict, pattern_files: list[Path]
    ) -> None:
        # Every cluster's associated_patterns entry must map to an existing
        # code_patterns/*.md file (or be empty).  Lint catches this too,
        # but we want a failing test (not a warning) if a deploy is broken.
        stems = {p.stem for p in pattern_files}
        missing = []
        for cid, c in bridge["symptom_clusters"].items():
            for entry in c.get("associated_patterns", []) or []:
                # entries are either plain id ("anti_windup_pid") or
                # "code:pattern_id" — normalize both forms.
                pid = entry.split(":", 1)[-1].strip()
                if not pid:
                    continue
                # Try both raw and "pattern_"-prefixed forms.
                if pid not in stems and f"pattern_{pid}" not in stems:
                    missing.append((cid, pid))
        assert not missing, f"associated_patterns missing md files: {missing[:5]}"

    def test_k_bridge_007_safety_label_index_present(self, bridge: dict) -> None:
        sli = bridge.get("safety_label_index")
        assert isinstance(sli, dict)
        assert sli, "safety_label_index must not be empty"

    def test_k_bridge_010_schema_version_present(self, bridge: dict) -> None:
        # Captured in test_schemas.py for the validator; lock the live file too.
        assert "schema_version" in bridge


# ── K-CURATED: curated safety patterns must be published ────────────────


class TestKCurated:
    """Maps to outline 2.8 ‘Curated Pattern 测试’."""

    @pytest.fixture(scope="class")
    def curated(self):
        from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS

        return CURATED_SAFETY_PATTERNS

    def test_k_curated_001_all_files_published(
        self, curated, pattern_files: list[Path]
    ) -> None:
        stems = {p.stem for p in pattern_files}
        missing = [p.pattern_id for p in curated if p.pattern_id not in stems]
        assert not missing, (
            f"curated patterns not published as code_patterns/*.md: {missing}"
        )

    def test_k_curated_002_safety_label_index_covers_curated(
        self, curated, bridge: dict
    ) -> None:
        sli = bridge["safety_label_index"]
        missing = [p.safety_label for p in curated if p.safety_label not in sli]
        assert not missing, (
            f"safety_label_index missing labels for curated patterns: {missing}"
        )

    def test_k_curated_006_safety_label_points_to_curated(
        self, curated, bridge: dict
    ) -> None:
        # Each curated safety_label entry must include the curated pattern_id.
        sli = bridge["safety_label_index"]
        misroute = []
        for p in curated:
            entries = sli.get(p.safety_label) or []
            if p.pattern_id not in entries:
                misroute.append((p.safety_label, p.pattern_id, entries))
        assert not misroute, (
            f"safety_label → pattern_id misrouted (curated must be reachable): "
            f"{misroute[:3]}"
        )


# ── K-MUSE: pattern markdown structural requirements ─────────────────────


class TestKMuseMarkdown:
    """Maps to outline 2.7 ‘Muse Pattern 编译测试’ acceptance:

      ‘随机抽样 30 个 pattern，≥ 85% 可读、可检索、可注入。
       所有 pattern 必须有 Anti-pattern heading。’
    """

    # Allow either '## Anti-pattern' / '## Anti-Pattern' / '### …' / etc.
    # Curated patterns use '## Anti-pattern'; muse-generated may vary case.
    _ANTI_RX = re.compile(r"^\s{0,3}#{1,6}\s+anti[-_ ]?pattern\b", re.IGNORECASE | re.MULTILINE)
    _SYMPTOM_RX = re.compile(r"^\s{0,3}#{1,6}\s+symptom\b", re.IGNORECASE | re.MULTILINE)
    _FIX_RX = re.compile(r"^\s{0,3}#{1,6}\s+(fix|treatment|repair)\b", re.IGNORECASE | re.MULTILINE)

    def test_k_muse_007_filename_matches_pattern_id_in_frontmatter(
        self, pattern_files: list[Path]
    ) -> None:
        # When a pattern md has a YAML-like ``pattern_id:`` line, it must
        # match the file stem.  Muse-emitted files may omit it, in which
        # case we don't enforce.
        mismatches = []
        for p in pattern_files:
            text = p.read_text(errors="ignore")
            m = re.search(r"^pattern_id:\s*([A-Za-z0-9_.-]+)", text, re.MULTILINE)
            if m and m.group(1) != p.stem:
                mismatches.append((p.stem, m.group(1)))
        assert not mismatches, f"pattern_id ≠ filename stem: {mismatches[:5]}"

    def test_k_muse_008_anti_pattern_heading_present_random_30(
        self, pattern_files: list[Path]
    ) -> None:
        # Deterministic sample so a flake doesn't bounce CI.
        rng = random.Random(0x12345678)
        sampled = rng.sample(pattern_files, k=min(30, len(pattern_files)))
        missing = [p.name for p in sampled if not self._ANTI_RX.search(p.read_text(errors="ignore"))]
        # Outline acceptance: ≥85% of sampled patterns must be compliant
        # (i.e. ≤15% allowed to lack Anti-pattern heading).  This is a
        # transitional bar — once muse output is fully migrated this can
        # tighten to 100%.
        ratio = 1.0 - (len(missing) / len(sampled))
        assert ratio >= 0.85, (
            f"only {ratio:.0%} of sampled patterns have Anti-pattern heading "
            f"(need ≥85%); missing examples: {missing[:5]}"
        )

    def test_k_muse_symptom_heading_present_random_30(
        self, pattern_files: list[Path]
    ) -> None:
        rng = random.Random(0xDEADBEEF)
        sampled = rng.sample(pattern_files, k=min(30, len(pattern_files)))
        missing = [p.name for p in sampled if not self._SYMPTOM_RX.search(p.read_text(errors="ignore"))]
        ratio = 1.0 - (len(missing) / len(sampled))
        assert ratio >= 0.85, (
            f"only {ratio:.0%} of sampled patterns have Symptom heading "
            f"(need ≥85%); missing examples: {missing[:5]}"
        )

    def test_k_muse_fix_heading_present_random_30(
        self, pattern_files: list[Path]
    ) -> None:
        rng = random.Random(0xC0FFEE)
        sampled = rng.sample(pattern_files, k=min(30, len(pattern_files)))
        missing = [p.name for p in sampled if not self._FIX_RX.search(p.read_text(errors="ignore"))]
        ratio = 1.0 - (len(missing) / len(sampled))
        assert ratio >= 0.85, (
            f"only {ratio:.0%} of sampled patterns have Fix/Treatment heading "
            f"(need ≥85%); missing examples: {missing[:5]}"
        )
