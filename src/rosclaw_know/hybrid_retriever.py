"""Sprint 5: hybrid retrieval for PatternCardV2 (plan §6.3).

Replaces the v1 "pure semantic similarity" ranker with a weighted blend of
six signals, plus a contraindication penalty and demotion exclusion.  The
formula is the one from plan §6.3:

    score = 0.35 * semantic_similarity(query.text, pattern.text)
          + 0.15 * bm25(query.keywords, pattern.keywords)
          + 0.15 * match(query.task_family, pattern.task_families)
          + 0.10 * match(query.embodiment_type, pattern.embodiments)
          + 0.10 * match(query.verifier_signal, pattern.expected_signals)
          + 0.10 * evidence_score(pattern.win_rate, pattern.uplift_n)
          - 0.20 * contraindication_match(query, pattern)

No external embedding service is required: the default semantic_fn is a
token-Jaccard fallback so the retriever runs offline and in CI.  Real
embeddings can be plugged in via ``semantic_fn`` for production use.

Design notes
------------
* The ranker is *pure*: same query + same pattern + same semantic_fn →
  same score.  This makes the acceptance tests deterministic.
* The score is **not** normalised to [0, 1]; it's a sum of weighted
  signals.  Use :func:`top_k` to consume — the absolute magnitude only
  matters when comparing patterns under the same query.
* ``priority == -1`` (demoted) patterns are excluded from :func:`top_k`
  by default (plan §Sprint 5 acceptance #4).  Pass
  ``include_demoted=True`` to override.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .schemas import EmbodimentType, EvidenceBlock, PatternCardV2

log = logging.getLogger("rosclaw_know.hybrid_retriever")


# ── Query dataclass ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RankerQuery:
    """A single ranker query.

    All fields are optional.  Missing fields produce a zero contribution
    from their corresponding component — they don't change the ordering
    of other components.
    """

    text: str = ""
    """Free-form query text — what the agent reports as its symptom or goal."""

    keywords: tuple[str, ...] = ()
    """Pre-tokenised keywords.  When empty, derived from ``text``."""

    task_family: str | None = None
    """e.g. ``robotics_optimization``.  Boosts matching patterns."""

    embodiment_type: EmbodimentType | None = None
    """The robot/system the agent is controlling."""

    verifier_signals: tuple[str, ...] = ()
    """Signals from the verifier (e.g. ``settling_time_below_threshold``)."""

    contraindications: tuple[str, ...] = ()
    """Constraints the agent must respect — patterns mentioning these
    things in *their* contraindications are penalised."""

    domain_hint: str | None = None
    """Optional domain bucket to bias the ranker.  Soft, not strict."""


# ── default semantic fallback ────────────────────────────────────────────


_WORD_RE = re.compile(r"[a-z][a-z0-9_]+")
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "this", "that", "they", "their",
        "are", "was", "were", "but", "not", "into", "than", "then", "have",
        "has", "had", "such", "also", "any", "all", "can", "may", "use",
        "using", "used", "via", "due", "should", "will", "would", "could",
        "of", "in", "on", "at", "by", "or", "is", "be", "an", "as", "it",
        "to", "we", "you", "your", "our", "if", "so",
    }
)


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokens with stopwords removed."""
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS]


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Token-set Jaccard similarity in [0, 1]."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def default_semantic_fn(query_text: str, pattern_text: str) -> float:
    """Offline semantic-similarity placeholder.

    Token-Jaccard between de-stopworded word sets.  Production deploys
    can replace this with a real embedding cosine via the ``semantic_fn``
    parameter on :func:`rank_pattern`.
    """
    return _jaccard(_tokenize(query_text), _tokenize(pattern_text))


# ── individual signals ──────────────────────────────────────────────────


def _pattern_full_text(p: PatternCardV2) -> str:
    """The text bag we compare against for semantic similarity."""
    return " ".join(
        [
            p.symptom,
            p.diagnosis,
            p.next_experiment,
            " ".join(p.expected_verifier_signals),
        ]
    )


def _pattern_keywords(p: PatternCardV2) -> list[str]:
    """Keyword bag for BM25-style scoring."""
    return _tokenize(
        " ".join(
            [
                p.symptom,
                p.diagnosis,
                " ".join(p.task_families),
                " ".join(p.expected_verifier_signals),
                p.code_target,
            ]
        )
    )


def _bm25_like(query_kw: Sequence[str], doc_kw: Sequence[str]) -> float:
    """Lightweight BM25-style overlap score normalised to [0, 1].

    We don't have an inverse-doc-freq table at runtime, so we use a
    simplified ``|q∩d| / sqrt(|q| * |d|)`` (cosine over indicator
    vectors).  Empirically gives almost-identical ordering to BM25 when
    query and doc lengths are small.
    """
    if not query_kw or not doc_kw:
        return 0.0
    q, d = set(query_kw), set(doc_kw)
    inter = len(q & d)
    return inter / math.sqrt(len(q) * len(d))


def _task_family_match(query: RankerQuery, p: PatternCardV2) -> float:
    """Returns 1.0 when ``query.task_family`` is in ``p.task_families``."""
    if not query.task_family:
        return 0.0
    return 1.0 if query.task_family in p.task_families else 0.0


def _embodiment_match(query: RankerQuery, p: PatternCardV2) -> float:
    if query.embodiment_type is None:
        return 0.0
    return 1.0 if query.embodiment_type in p.embodiment_types else 0.0


def _verifier_signal_match(query: RankerQuery, p: PatternCardV2) -> float:
    """Token-overlap of the verifier signals (returns Jaccard-like score)."""
    if not query.verifier_signals or not p.expected_verifier_signals:
        return 0.0
    qs = _tokenize(" ".join(query.verifier_signals))
    ps = _tokenize(" ".join(p.expected_verifier_signals))
    return _jaccard(qs, ps)


def _evidence_score(ev: EvidenceBlock) -> float:
    """Combine win_rate and sample count into a [0, 1] confidence score.

    ``win_rate`` alone is brittle when ``n`` is tiny.  Weight by
    ``log1p(n) / log1p(20)`` so 0 samples → 0, 20 samples → fully
    weighted.  Empirically chosen — Sprint 6 evidence loop will refine.
    """
    if ev.n <= 0:
        return 0.0
    sample_weight = math.log1p(ev.n) / math.log1p(20)
    return min(1.0, ev.win_rate * sample_weight)


def _contraindication_match(query: RankerQuery, p: PatternCardV2) -> float:
    """Returns 1.0 if ANY of query.contraindications token-matches a
    pattern contraindication entry, else 0.0.

    Use case: the agent is in a `safety_critical` context and the
    pattern has "do not raise Ki" in its contraindications — that
    matches the query's "increase_gain" intent → penalise.
    """
    if not query.contraindications or not p.contraindications:
        return 0.0
    q_tokens = set(_tokenize(" ".join(query.contraindications)))
    for entry in p.contraindications:
        if q_tokens & set(_tokenize(entry)):
            return 1.0
    return 0.0


# ── public ranker ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreBreakdown:
    """Component-by-component breakdown for debugging / explanation."""

    semantic: float = 0.0
    bm25: float = 0.0
    task_family: float = 0.0
    embodiment: float = 0.0
    verifier_signal: float = 0.0
    evidence: float = 0.0
    contraindication: float = 0.0
    total: float = 0.0


def rank_pattern(
    query: RankerQuery,
    pattern: PatternCardV2,
    *,
    semantic_fn: Callable[[str, str], float] | None = None,
) -> ScoreBreakdown:
    """Score a single pattern against a query.

    See module-level docstring for the formula.  When ``semantic_fn`` is
    None the offline token-Jaccard fallback is used.
    """
    sem_fn = semantic_fn or default_semantic_fn

    query_text = query.text or " ".join(query.keywords)
    query_keywords = (
        list(query.keywords) if query.keywords else _tokenize(query_text)
    )

    semantic = sem_fn(query_text, _pattern_full_text(pattern))
    bm25 = _bm25_like(query_keywords, _pattern_keywords(pattern))
    fam = _task_family_match(query, pattern)
    embo = _embodiment_match(query, pattern)
    ver = _verifier_signal_match(query, pattern)
    ev = _evidence_score(pattern.evidence)
    contra = _contraindication_match(query, pattern)

    total = (
        0.35 * semantic
        + 0.15 * bm25
        + 0.15 * fam
        + 0.10 * embo
        + 0.10 * ver
        + 0.10 * ev
        - 0.20 * contra
    )

    return ScoreBreakdown(
        semantic=semantic,
        bm25=bm25,
        task_family=fam,
        embodiment=embo,
        verifier_signal=ver,
        evidence=ev,
        contraindication=contra,
        total=total,
    )


def top_k(
    query: RankerQuery,
    patterns: Iterable[PatternCardV2],
    *,
    k: int = 5,
    semantic_fn: Callable[[str, str], float] | None = None,
    include_demoted: bool = False,
    min_score: float | None = None,
) -> list[tuple[PatternCardV2, ScoreBreakdown]]:
    """Rank ``patterns`` against ``query`` and return the top-k.

    Demoted patterns (priority == -1) are excluded unless
    ``include_demoted=True``.  Patterns scoring below ``min_score``
    (when set) are filtered out — useful when the caller wants to
    refuse low-confidence recommendations.
    """
    scored: list[tuple[PatternCardV2, ScoreBreakdown]] = []
    for p in patterns:
        if not include_demoted and p.priority == -1:
            continue
        sb = rank_pattern(query, p, semantic_fn=semantic_fn)
        if min_score is not None and sb.total < min_score:
            continue
        scored.append((p, sb))
    scored.sort(key=lambda t: t[1].total, reverse=True)
    return scored[:k]


__all__ = [
    "RankerQuery",
    "ScoreBreakdown",
    "default_semantic_fn",
    "rank_pattern",
    "top_k",
]
