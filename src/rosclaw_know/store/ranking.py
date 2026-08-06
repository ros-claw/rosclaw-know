"""Deterministic exact/text/vector scoring and reciprocal-rank fusion."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]*|[\u4e00-\u9fff]+|\d+")


def tokenize(value: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(value)]


def exact_score(query: str, text: str) -> float:
    normalized_query = " ".join(query.casefold().split())
    normalized_text = " ".join(text.casefold().split())
    if not normalized_query:
        return 0.0
    if normalized_query in normalized_text:
        return 1.0
    query_tokens = set(tokenize(normalized_query))
    if not query_tokens:
        return 0.0
    return len(query_tokens & set(tokenize(normalized_text))) / len(query_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def reciprocal_rank_fusion(
    rankings: Iterable[list[str]], *, rank_constant: int = 60
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] += 1.0 / (rank_constant + rank)
    return dict(scores)
