"""Tests for the curated conflict detector."""
from __future__ import annotations

from rosclaw_know.curated_conflict_detector import (
    Conflict,
    detect_conflicts,
    format_report,
)
from rosclaw_know.curated_patterns import CURATED_SAFETY_PATTERNS, CuratedPattern


def _make_pattern(**kw) -> CuratedPattern:
    """Build a minimal-but-valid CuratedPattern for tests."""
    defaults = {
        "pattern_id": "test_p",
        "standard_name": "default standard name",
        "domain": "Test_Domain",
        "safety_label": "Test_Label",
        "fix_pattern": "x",
        "failed_attempt": "y",
        "before_code": "old",
        "after_code": "new",
        "matched_keywords": ["a", "b"],
        "cross_domain_hints": [],
    }
    defaults.update(kw)
    return CuratedPattern(**defaults)


class TestPatternIdDuplicate:
    def test_distinct_ids_no_conflict(self):
        p1 = _make_pattern(pattern_id="p1", safety_label="L1")
        p2 = _make_pattern(pattern_id="p2", safety_label="L2")
        assert detect_conflicts([p1, p2]) == []

    def test_duplicate_pattern_id_detected(self):
        p1 = _make_pattern(pattern_id="dup", safety_label="L1", standard_name="A B C D E")
        p2 = _make_pattern(pattern_id="dup", safety_label="L2", standard_name="X Y Z")
        conflicts = detect_conflicts([p1, p2])
        kinds = {c.kind for c in conflicts}
        assert "pattern_id_duplicate" in kinds


class TestSafetyLabelCollision:
    def test_unique_labels_no_collision(self):
        p1 = _make_pattern(pattern_id="p1", safety_label="L1")
        p2 = _make_pattern(pattern_id="p2", safety_label="L2")
        ko = [c for c in detect_conflicts([p1, p2]) if c.kind == "safety_label"]
        assert ko == []

    def test_two_curated_share_label_detected(self):
        # The K-CURATED-006 case: two curated for Memory_Exhaustion
        p1 = _make_pattern(pattern_id="kv_cache", safety_label="Memory_Exhaustion")
        p2 = _make_pattern(pattern_id="flash_attn", safety_label="Memory_Exhaustion")
        ko = [c for c in detect_conflicts([p1, p2]) if c.kind == "safety_label"]
        assert len(ko) == 1
        assert set(ko[0].pattern_ids) == {"kv_cache", "flash_attn"}
        assert "Memory_Exhaustion" in ko[0].detail

    def test_three_curated_share_label(self):
        p1 = _make_pattern(pattern_id="p1", safety_label="L")
        p2 = _make_pattern(pattern_id="p2", safety_label="L")
        p3 = _make_pattern(pattern_id="p3", safety_label="L")
        ko = [c for c in detect_conflicts([p1, p2, p3]) if c.kind == "safety_label"]
        assert len(ko) == 1
        assert set(ko[0].pattern_ids) == {"p1", "p2", "p3"}


class TestStandardNameOverlap:
    def test_no_overlap_no_conflict(self):
        p1 = _make_pattern(pattern_id="p1", standard_name="alpha beta gamma delta")
        p2 = _make_pattern(pattern_id="p2", standard_name="zulu yankee xray whiskey")
        ko = [c for c in detect_conflicts([p1, p2]) if c.kind == "standard_name_overlap"]
        assert ko == []

    def test_high_overlap_detected_at_threshold(self):
        # 5 shared meaningful tokens crosses default threshold (4).
        p1 = _make_pattern(
            pattern_id="p1",
            standard_name="quadruped gait stability terrain perception slip",
        )
        p2 = _make_pattern(
            pattern_id="p2",
            standard_name="quadruped gait stability terrain perception locomotion",
        )
        ko = [c for c in detect_conflicts([p1, p2]) if c.kind == "standard_name_overlap"]
        assert len(ko) == 1
        assert set(ko[0].pattern_ids) == {"p1", "p2"}
        assert ">= 4 tokens" not in ko[0].detail  # detail should say "share N tokens"
        # The detail should at least mention the shared count
        assert "share" in ko[0].detail.lower()

    def test_threshold_respected(self):
        # 3 shared tokens — below default threshold 4 → no conflict
        p1 = _make_pattern(pattern_id="p1", standard_name="alpha beta gamma delta")
        p2 = _make_pattern(pattern_id="p2", standard_name="alpha beta gamma zulu")
        ko = [c for c in detect_conflicts([p1, p2]) if c.kind == "standard_name_overlap"]
        assert ko == []

    def test_custom_threshold(self):
        # Lower threshold to 2 — 3 shared tokens now triggers
        p1 = _make_pattern(pattern_id="p1", standard_name="aaa bbb ccc ddd")
        p2 = _make_pattern(pattern_id="p2", standard_name="aaa bbb ccc xxx")
        ko = [c for c in detect_conflicts([p1, p2], standard_name_overlap_threshold=2)
              if c.kind == "standard_name_overlap"]
        assert len(ko) == 1

    def test_short_tokens_ignored(self):
        # _tokens drops len < 3 tokens — "an", "or", "in" don't count
        p1 = _make_pattern(pattern_id="p1", standard_name="an or in if to")
        p2 = _make_pattern(pattern_id="p2", standard_name="an or in if to")
        ko = [c for c in detect_conflicts([p1, p2]) if c.kind == "standard_name_overlap"]
        assert ko == []  # short-token soup → empty set → no overlap


class TestFormatReport:
    def test_empty_returns_empty_string(self):
        assert format_report([]) == ""

    def test_groups_by_kind(self):
        conflicts = [
            Conflict(kind="safety_label", pattern_ids=("p1", "p2"), detail="shared L1"),
            Conflict(kind="pattern_id_duplicate", pattern_ids=("dup",), detail="2 objs"),
            Conflict(kind="standard_name_overlap", pattern_ids=("p3", "p4"), detail="share 5"),
        ]
        out = format_report(conflicts)
        # All 3 kinds appear in fixed order: dup → safety_label → overlap
        assert out.find("pattern_id_duplicate") < out.find("safety_label") < out.find("standard_name_overlap")
        assert "shared L1" in out
        assert "2 objs" in out
        assert "share 5" in out


class TestLiveRegistry:
    """Verify behaviour on the actual CURATED_SAFETY_PATTERNS."""

    def test_no_pattern_id_duplicates(self):
        """The shipped 14 curated must never have duplicate IDs."""
        conflicts = detect_conflicts(CURATED_SAFETY_PATTERNS)
        dups = [c for c in conflicts if c.kind == "pattern_id_duplicate"]
        assert dups == [], format_report(dups)

    def test_known_safety_label_collisions_documented(self):
        """The Memory_Exhaustion collision is intentional. Document any others."""
        conflicts = detect_conflicts(CURATED_SAFETY_PATTERNS)
        label_clashes = [c for c in conflicts if c.kind == "safety_label"]
        # Build a set of {(pattern_ids tuple, label)} that we expect
        # Memory_Exhaustion is the documented case from K-CURATED-006
        labels = {
            c.detail.split("safety_label=")[1].split("'")[1]
            for c in label_clashes
        }
        # If new collisions appear, surface them — but Memory_Exhaustion
        # is allowed.
        unexpected = labels - {"Memory_Exhaustion"}
        assert not unexpected, (
            f"unexpected safety_label collisions in live registry: {unexpected}. "
            f"If intentional, add them to the allowed set; otherwise rename "
            f"one of the colliding patterns' safety_label."
        )
