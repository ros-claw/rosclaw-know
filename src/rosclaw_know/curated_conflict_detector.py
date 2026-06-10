"""Curated registry conflict detector.

docs/know-how下一步建议.md §7 — proactive detection of collisions in
``CURATED_SAFETY_PATTERNS`` before they ship. The K-CURATED-006 incident
(Memory_Exhaustion claimed by both sliding_window_kv_cache and
flash_attention_tiled_softmax) was a silent last-wins bug masked by a
``dict[str, str]`` safety_label_index. The index is now list-valued, but
authors still need to be told "these two curated will collide at
runtime" the moment they add the second one — not when a test fails or
a paired-AB shows a routing surprise.

What this module detects (current pass):

1. **safety_label collisions** — N curated patterns share one safety_label.
   The runtime's exact-string-match path returns whichever wins ANN
   ranking; the loser becomes effectively invisible to its claimed
   label. NOT necessarily a bug — sometimes two curated genuinely apply
   to the same symptom class (KV-cache OOM vs attention-matrix OOM both
   are Memory_Exhaustion) — but the author should acknowledge it.

2. **standard_name n-gram overlap** — two curated whose standard_name
   shares ≥ N tokens. High overlap means cosine similarity will be
   high, so the curated pair may oscillate in top-1 ranking.

3. **pattern_id duplicates** — two CuratedPattern objects with the same
   pattern_id. The publisher would silently overwrite one. This should
   never happen; if it does the registry is broken.

What this module does NOT do:
- It does not block CI. It returns conflict objects; the caller decides
  whether to log/warn/fail.
- It does not check synth↔curated collisions (the publisher's
  curated-rescue path handles those at runtime).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .curated_patterns import CuratedPattern


@dataclass(frozen=True)
class Conflict:
    """Single conflict between two-or-more curated patterns."""

    kind: str  # "safety_label", "standard_name_overlap", "pattern_id_duplicate"
    pattern_ids: tuple[str, ...]
    detail: str  # human-readable description of what the conflict is

    def __str__(self) -> str:  # pragma: no cover — convenience for logs
        return f"[{self.kind}] {', '.join(self.pattern_ids)}: {self.detail}"


def _tokens(text: str) -> set[str]:
    """Crude tokenizer for n-gram overlap. Lowercases + alphanumeric only."""
    return {t for t in (
        "".join(c if c.isalnum() else " " for c in text.lower()).split()
    ) if len(t) >= 3}


def detect_conflicts(
    patterns: Iterable[CuratedPattern],
    *,
    standard_name_overlap_threshold: int = 4,
) -> list[Conflict]:
    """Return all conflicts in the curated registry.

    The standard-name overlap threshold is intentionally conservative
    (≥4 shared tokens). Lower values fire on coincidental words like
    "the" + "with" + "in" + "for"; higher values miss genuine
    near-duplicates. 4 is empirically the sweet spot on the current
    14-curated set (zero false positives observed).
    """
    patterns = list(patterns)
    conflicts: list[Conflict] = []

    # 1. pattern_id duplicates — the publisher would silently overwrite,
    #    so this is the highest-severity check.
    seen: dict[str, list[CuratedPattern]] = defaultdict(list)
    for p in patterns:
        seen[p.pattern_id].append(p)
    for pid, group in seen.items():
        if len(group) > 1:
            conflicts.append(
                Conflict(
                    kind="pattern_id_duplicate",
                    pattern_ids=tuple(pid for _ in group),
                    detail=f"{len(group)} CuratedPattern objects share pattern_id={pid!r}",
                )
            )

    # 2. safety_label collisions.
    by_label: dict[str, list[CuratedPattern]] = defaultdict(list)
    for p in patterns:
        by_label[p.safety_label].append(p)
    for label, group in by_label.items():
        if len(group) > 1:
            ids = tuple(sorted(p.pattern_id for p in group))
            conflicts.append(
                Conflict(
                    kind="safety_label",
                    pattern_ids=ids,
                    detail=(
                        f"{len(group)} curated share safety_label={label!r}; "
                        f"runtime safety_label_index returns the full list, but "
                        f"the author should confirm both genuinely apply."
                    ),
                )
            )

    # 3. standard_name token overlap above threshold. Pairwise so the
    #    Conflict points at exactly two patterns.
    for i, a in enumerate(patterns):
        a_toks = _tokens(a.standard_name)
        for b in patterns[i + 1 :]:
            if a.pattern_id == b.pattern_id:
                continue
            shared = a_toks & _tokens(b.standard_name)
            if len(shared) >= standard_name_overlap_threshold:
                conflicts.append(
                    Conflict(
                        kind="standard_name_overlap",
                        pattern_ids=tuple(sorted((a.pattern_id, b.pattern_id))),
                        detail=(
                            f"standard_names share {len(shared)} tokens "
                            f"(threshold {standard_name_overlap_threshold}): "
                            f"{sorted(shared)}"
                        ),
                    )
                )

    return conflicts


def format_report(conflicts: list[Conflict]) -> str:
    """Render a conflict list as a multi-line human-readable report.

    Returns an empty string when no conflicts (so callers can do
    ``if report: log.warning(report)``).
    """
    if not conflicts:
        return ""
    lines = [f"Curated registry: {len(conflicts)} conflict(s) detected"]
    by_kind: dict[str, list[Conflict]] = defaultdict(list)
    for c in conflicts:
        by_kind[c.kind].append(c)
    for kind in ("pattern_id_duplicate", "safety_label", "standard_name_overlap"):
        if kind not in by_kind:
            continue
        lines.append(f"  {kind}:")
        for c in by_kind[kind]:
            lines.append(f"    - {', '.join(c.pattern_ids)}: {c.detail}")
    return "\n".join(lines)


__all__ = ["Conflict", "detect_conflicts", "format_report"]
