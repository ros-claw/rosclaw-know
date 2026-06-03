"""Sprint 7: build agent pre-flight :class:`TaskPack`s (plan §10, §11.7).

Given a :class:`TaskPackQuery` (the agent's target task + budget),
returns a :class:`TaskPack` containing:

* the matched :class:`TaskCard`'s constraints and verifier signature;
* the top-K :class:`PatternCardV2` recommendations from the
  :mod:`hybrid_retriever`, ranked by adjusted-uplift evidence;
* an ordered exploration plan staggered across the iteration budget;
* the union of patterns + failures contraindications as
  ``anti_patterns``;
* a token-count guard so the rendered pack stays under
  :attr:`TaskPackQuery.max_tokens` (plan §Sprint 7 ceiling = 1200).

The builder is **pure** — no IO inside :func:`build_task_pack`.  The
caller is responsible for loading the catalog (typically once at
process start, then cached).  This makes the unit tests deterministic
and lets :class:`scripts/build_task_pack.py` round-trip its output to
JSON without touching the network.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence

from .hybrid_retriever import RankerQuery, ScoreBreakdown, top_k
from .schemas import (
    EmbodimentType,
    FailureMode,
    PatternCardV2,
    TaskCard,
    TaskPack,
    TaskPackPatternRef,
    TaskPackQuery,
)

log = logging.getLogger("rosclaw_know.task_pack_builder")


# ── exceptions ──────────────────────────────────────────────────────────


class TaskCardNotFoundError(LookupError):
    """Raised when no TaskCard matches the :class:`TaskPackQuery`."""


# ── default summary template ────────────────────────────────────────────


_DEFAULT_SUMMARY_TMPL = (
    "{task_name} task on benchmark {benchmark}: "
    "optimise {metric_name} ({objective_direction}) within "
    "{budget_iterations} iterations."
)


# ── task_family → embodiment_type hint (for the retriever) ──────────────

_FAMILY_TO_EMBODIMENT_TYPE: dict[str, EmbodimentType] = {
    "robotics_optimization": "manipulator",
    "kernel_engineering_optimization": "gpu_kernel",
    "cryptographic_optimization": "gpu_kernel",
    "sustainable_data_center_control_optimization": "data_center",
    "computer_systems_optimization": "data_center",
    "optics_optimization": "optical_system",
    "additive_manufacturing_optimization": "manipulator",
}


# ── helpers ──────────────────────────────────────────────────────────────


def _normalise(s: str) -> str:
    """Lower + strip + collapse non-alphanum to underscore.

    Inserts a separator at camelCase boundaries first so the downstream
    tokeniser can split words like ``PIDTuning`` → ``pid_tuning``.
    """
    # Insert separator before any uppercase letter that follows a
    # lowercase or another uppercase-letter→lowercase boundary.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", s)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", spaced)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def _tokenise(s: str) -> set[str]:
    """Like :func:`_normalise` but also splits at letter↔digit boundaries.

    ``aes128`` → ``{"aes", "128"}``; ``crypto_aes128`` →
    ``{"crypto", "aes", "128"}``.  Enables the token-overlap heuristic
    in :func:`_match_task_card` to bridge stylistic differences between
    the agent-supplied ``task_name`` and the canonical ``TaskCard``
    naming (``AES-128`` vs ``aes128``).
    """
    norm = _normalise(s)
    out: set[str] = set()
    for token in norm.split("_"):
        if not token:
            continue
        # Split at letter↔digit boundaries.
        sub = re.findall(r"[a-z]+|[0-9]+", token)
        out.update(sub)
    return out


def _match_task_card(
    query: TaskPackQuery, catalog: Sequence[TaskCard]
) -> TaskCard | None:
    """Best-effort task lookup.

    Tries (in order):
    1. exact ``id`` match on ``task_<query>``;
    2. case-insensitive substring on ``task_name`` (e.g. ``pid_tuning``
       matches ``PIDTuning``);
    3. ``task_name`` token overlap fallback;
    4. ``task_family`` substring (last-resort family match — useful when
       the agent only knows the family it's in).
    """
    if not catalog:
        return None

    needle = _normalise(query.task_name)

    # Step 1 — exact id like "task_pid_tuning_..." or "task_robotics_pid_tuning"
    for card in catalog:
        nid = _normalise(card.id)
        if needle and (nid.endswith(f"_{needle}") or nid == f"task_{needle}"):
            return card
        if needle and needle in nid:
            return card

    # Step 2 — substring on task_name
    for card in catalog:
        if needle and needle in _normalise(card.task_name):
            return card

    # Step 3 — token overlap (≥ 2 tokens shared) with letter/digit splitting.
    query_tokens = _tokenise(query.task_name)
    if len(query_tokens) >= 2:
        # First try task_name overlap…
        for card in catalog:
            card_tokens = _tokenise(card.task_name)
            if len(query_tokens & card_tokens) >= 2:
                return card
        # …then task_family + task_name overlap (so e.g. crypto_aes128 finds
        # AES-128 via family=cryptographic_optimization).
        for card in catalog:
            blended = _tokenise(card.task_name) | _tokenise(card.task_family)
            if len(query_tokens & blended) >= 2:
                return card

    # Step 4 — fall back to family
    if query.benchmark:
        bn = _normalise(query.benchmark)
        for card in catalog:
            if bn and bn in _normalise(card.benchmark or ""):
                # any card in that benchmark family is better than nothing
                return card
    return None


def _build_summary(query: TaskPackQuery, card: TaskCard) -> str:
    """Compress the task description into 1-2 sentences."""
    objective = (
        query.objective_direction or card.objective_direction
    )
    metric = query.metric_name or card.metric_name
    benchmark = query.benchmark or card.benchmark or "ROSClaw Arena"
    summary = _DEFAULT_SUMMARY_TMPL.format(
        task_name=card.task_name,
        benchmark=benchmark,
        metric_name=metric,
        objective_direction=objective,
        budget_iterations=query.budget_iterations,
    )
    if card.baseline_description:
        # First sentence of the baseline description for context.
        first = card.baseline_description.split(".")[0].strip()
        if first and len(first) < 200:
            summary += f"  Baseline context: {first}."
    return summary


def _build_recommendations(
    query: TaskPackQuery,
    card: TaskCard,
    patterns: Sequence[PatternCardV2],
    failure_lookup: dict[str, FailureMode],
) -> list[tuple[TaskPackPatternRef, PatternCardV2, ScoreBreakdown]]:
    """Run the hybrid retriever and return ``[(ref, pattern, score), ...]``.

    The ``RankerQuery`` we pose blends:
      * the task summary + failure observable_signals (free-form text);
      * ``task_family`` from the matched card;
      * the embodiment hint from :data:`_FAMILY_TO_EMBODIMENT_TYPE`;
      * the verifier signature (verifier_type) as a signal.
    """
    obs_signals: list[str] = []
    for fid in card.common_failure_modes:
        fm = failure_lookup.get(fid)
        if fm:
            obs_signals.extend(fm.observable_signals)
    query_text = " ".join(
        [
            card.task_name,
            card.task_family,
            card.baseline_description or "",
            " ".join(obs_signals),
        ]
    )
    embodiment = _FAMILY_TO_EMBODIMENT_TYPE.get(card.task_family)
    rq = RankerQuery(
        text=query_text,
        task_family=card.task_family,
        embodiment_type=embodiment,
        verifier_signals=tuple(obs_signals[:5]) if obs_signals else (),
        domain_hint=card.domain,
    )
    hits = top_k(rq, patterns, k=query.top_k_patterns)
    out: list[tuple[TaskPackPatternRef, PatternCardV2, ScoreBreakdown]] = []
    for pattern, sb in hits:
        reason = (
            f"matches task_family={card.task_family}"
            if sb.task_family > 0
            else f"semantic relevance {sb.semantic:.2f}"
        )
        ref = TaskPackPatternRef(
            pattern_id=pattern.id,
            score=round(sb.total, 4),
            reason=reason,
            domain=pattern.domain,
        )
        out.append((ref, pattern, sb))
    return out


def _build_exploration_plan(
    query: TaskPackQuery,
    selections: Sequence[tuple[TaskPackPatternRef, PatternCardV2, ScoreBreakdown]],
) -> list[str]:
    """Stagger the recommended patterns across the iteration budget.

    Plan §10 example shape:

        First establish a stable low-gain baseline.
        Then test zero integral gains.
        Then tune damping terms.

    For Sprint 7 we keep it deterministic: split the budget into N+1
    bands (baseline → pattern 1 → pattern 2 → …), each band gets a
    one-line directive built from the pattern's ``next_experiment``.
    """
    if not selections:
        return ["Establish a baseline run and observe verifier signals first."]
    bands = max(1, len(selections) + 1)
    iters_per = max(1, query.budget_iterations // bands)

    plan: list[str] = []
    plan.append(
        f"Iter 1-{iters_per}: establish a baseline run and confirm the "
        f"verifier signals before any change."
    )
    start = iters_per + 1
    for i, (ref, pattern, _sb) in enumerate(selections, start=1):
        # Use the first sentence of next_experiment, trimmed.  The
        # pattern's Markdown bullet prefix (`- `) is stripped so the
        # rendered exploration plan stays single-level.
        first_sentence = (pattern.next_experiment.split(".")[0]).strip()
        first_sentence = re.sub(r"^[-*+]\s+", "", first_sentence)
        if not first_sentence:
            first_sentence = pattern.symptom.split(".")[0].strip()
        action = first_sentence or f"apply pattern {pattern.id}"
        end = start + iters_per - 1
        if i == len(selections):
            end = query.budget_iterations
        plan.append(
            f"Iter {start}-{end}: try [{ref.pattern_id}] — {action}."
        )
        start = end + 1
    return plan


def _collect_anti_patterns(
    selections: Sequence[tuple[TaskPackPatternRef, PatternCardV2, ScoreBreakdown]],
    failure_lookup: dict[str, FailureMode],
    card: TaskCard,
) -> list[str]:
    """Union of pattern.contraindications + failure.contraindications."""
    seen: set[str] = set()
    out: list[str] = []
    for _ref, p, _sb in selections:
        for entry in p.contraindications + p.anti_patterns:
            key = entry.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(entry.strip())
    for fid in card.common_failure_modes:
        fm = failure_lookup.get(fid)
        if fm is None:
            continue
        for entry in fm.contraindications:
            key = entry.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(entry.strip())
    return out


def _collect_expected_signals(
    selections: Sequence[tuple[TaskPackPatternRef, PatternCardV2, ScoreBreakdown]],
    card: TaskCard,
) -> list[str]:
    """Build the expected_signals list from verifier + patterns."""
    seen: set[str] = set()
    out: list[str] = []
    # Validity / verifier baseline
    base = f"verifier returns valid for the chosen {card.verifier_type}"
    out.append(base)
    seen.add(base.lower())
    # Pattern-supplied signals
    for _ref, p, _sb in selections:
        for sig in p.expected_verifier_signals:
            key = sig.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(sig.strip())
    # Score-direction sanity
    direction = card.objective_direction
    metric = card.metric_name
    direction_signal = (
        f"{metric} {'increases' if direction == 'maximize' else 'decreases'} "
        f"vs baseline"
    )
    if direction_signal.lower() not in seen:
        out.append(direction_signal)
        seen.add(direction_signal.lower())
    return out


def _token_estimate(pack: TaskPack) -> int:
    """Rough word-count estimate of the rendered markdown.

    Conservative: every list entry's tokens count once.  Plan §Sprint 7
    cap is 1200 tokens — we use word count as a proxy (≈ 1 word per
    token for English engineering prose).
    """
    parts: list[str] = [
        pack.summary,
        " ".join(pack.hard_constraints),
        " ".join(p.pattern_id + " " + p.reason for p in pack.recommended_patterns),
        " ".join(pack.exploration_plan),
        " ".join(pack.anti_patterns),
        " ".join(pack.expected_signals),
    ]
    return sum(len(s.split()) for s in parts)


def _trim_to_budget(pack: TaskPack, max_tokens: int) -> TaskPack:
    """Iteratively trim list lengths until the pack fits.

    Trimming priority (least → most useful):
      1. ``anti_patterns`` (defensive context)
      2. ``expected_signals`` past the first 3
      3. ``hard_constraints`` past the first 5
      4. ``exploration_plan`` past 6
      5. ``recommended_patterns`` past 3
    """
    est = _token_estimate(pack)
    pack = pack.model_copy(update={"token_estimate": est})
    if est <= max_tokens:
        return pack

    # Trim in priority order.
    new_ap = list(pack.anti_patterns)[:6]
    new_es = list(pack.expected_signals)[:4]
    new_hc = list(pack.hard_constraints)[:5]
    new_xp = list(pack.exploration_plan)[:6]
    new_rp = list(pack.recommended_patterns)[: max(3, len(pack.recommended_patterns) // 2)]
    pack = pack.model_copy(update={
        "anti_patterns": new_ap,
        "expected_signals": new_es,
        "hard_constraints": new_hc,
        "exploration_plan": new_xp,
        "recommended_patterns": new_rp,
    })
    est = _token_estimate(pack)
    pack = pack.model_copy(update={"token_estimate": est})
    # If we're still over, hard-clip exploration plan and recommendations.
    if est > max_tokens:
        pack = pack.model_copy(update={
            "exploration_plan": pack.exploration_plan[:3],
            "recommended_patterns": pack.recommended_patterns[:3],
            "anti_patterns": pack.anti_patterns[:3],
            "expected_signals": pack.expected_signals[:3],
        })
        est = _token_estimate(pack)
        pack = pack.model_copy(update={"token_estimate": est})
    return pack


# ── main entry ───────────────────────────────────────────────────────────


def build_task_pack(
    query: TaskPackQuery,
    *,
    catalog: Sequence[TaskCard],
    patterns: Sequence[PatternCardV2],
    failures: Iterable[FailureMode] = (),
) -> TaskPack:
    """Build a :class:`TaskPack` for ``query``.

    Plan §10.1 reference implementation.  Pure function — no IO; pass
    pre-loaded catalogs.

    Raises :class:`TaskCardNotFoundError` if no TaskCard matches the
    query.  Callers can choose to retry with a broader benchmark, or
    fall back to a generic pack.
    """
    card = _match_task_card(query, catalog)
    if card is None:
        raise TaskCardNotFoundError(
            f"No TaskCard matches query task_name={query.task_name!r} "
            f"benchmark={query.benchmark!r}"
        )
    failure_lookup = {f.id: f for f in failures}

    summary = _build_summary(query, card)
    selections = _build_recommendations(query, card, patterns, failure_lookup)
    exploration = _build_exploration_plan(query, selections)
    anti = _collect_anti_patterns(selections, failure_lookup, card)
    signals = _collect_expected_signals(selections, card)

    benchmark = query.benchmark or card.benchmark or "rosclaw"
    pack_id = f"{_normalise(benchmark)}_{_normalise(card.task_name)}_v1"

    pack = TaskPack(
        task_pack_id=pack_id,
        summary=summary,
        objective_direction=(
            query.objective_direction or card.objective_direction
        ),
        metric_name=query.metric_name or card.metric_name,
        hard_constraints=list(card.hard_constraints),
        recommended_patterns=[ref for ref, _p, _sb in selections],
        exploration_plan=exploration,
        anti_patterns=anti,
        expected_signals=signals,
        source_task_card_id=card.id,
        source_failure_ids=list(card.common_failure_modes),
        budget_iterations=query.budget_iterations,
    )
    pack = _trim_to_budget(pack, query.max_tokens)
    return pack


# ── markdown render (for agent prompt) ──────────────────────────────────


def render_markdown(pack: TaskPack) -> str:
    """Render the pack as the markdown the agent's system prompt expects.

    Stable, deterministic format.  Intentionally terse — every line is
    something the agent should act on.
    """
    body: list[str] = []
    body.append(f"# Task Pack: {pack.task_pack_id}")
    body.append("")
    body.append(pack.summary)
    body.append("")
    body.append(f"**Objective**: {pack.objective_direction} `{pack.metric_name}`")
    body.append(f"**Budget**: {pack.budget_iterations} iterations")
    body.append("")
    if pack.hard_constraints:
        body.append("## Hard constraints")
        body.append("")
        for hc in pack.hard_constraints:
            body.append(f"- {hc}")
        body.append("")
    if pack.recommended_patterns:
        body.append("## Recommended patterns")
        body.append("")
        for r in pack.recommended_patterns:
            score_str = f" (score={r.score:.3f})" if r.score is not None else ""
            body.append(f"- `{r.pattern_id}`{score_str} — {r.reason}")
        body.append("")
    if pack.exploration_plan:
        body.append("## Exploration plan")
        body.append("")
        for step in pack.exploration_plan:
            body.append(f"- {step}")
        body.append("")
    if pack.anti_patterns:
        body.append("## Anti-patterns")
        body.append("")
        for ap in pack.anti_patterns:
            body.append(f"- {ap}")
        body.append("")
    if pack.expected_signals:
        body.append("## Expected verifier signals")
        body.append("")
        for sig in pack.expected_signals:
            body.append(f"- {sig}")
        body.append("")
    return "\n".join(body).rstrip() + "\n"


__all__ = [
    "TaskCardNotFoundError",
    "build_task_pack",
    "render_markdown",
]
