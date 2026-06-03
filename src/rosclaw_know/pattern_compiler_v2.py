"""Pattern Compiler V2 — turn :class:`CandidatePattern` (Sprint 3) into
:class:`PatternCardV2` markdown that an agent can read directly.

Sprint 4 deliverable (v1.5 plan §7.1, §11.6).

Where Sprint 3 emitted *evidence* (what we learned from agent
trajectories) as a YAML catalog, Sprint 4's job is to render those
findings as **action templates** the next agent can execute against.

Every emitted card has the structure required by plan §11.6::

    ---
    pattern_id: ...
    schema_version: "2.0"
    task_families: [...]
    embodiment_types: [...]
    artifact_languages: [...]
    priority: 0|1|-1|null
    source_quality: S|A|B|C|D
    evidence:
        n: int
        avg_uplift: float
        win_rate: float
        hint_use_rate: float
    ---

    # Title

    ## Symptom
    ## Diagnosis
    ## Preconditions
    ## Next Experiment
    ## Code Target
    ## Patch Sketch
    ## Expected Verifier Signal
    ## Anti-pattern
    ## Contraindications
    ## Cross-domain analogy           (optional)

The compiler is **pure-deterministic** — no LLM call, just structural
mapping.  Tests round-trip every section and the linter enforces
presence on the resulting markdown.

Failure-mode aware
------------------

If the CandidatePattern's :attr:`failure_id` matches an entry in
``data/assets/failure_taxonomy.yaml``, the compiler fills the
``Symptom`` and ``Diagnosis`` from the FailureMode's authoritative
text — that means the catalog's curated symptom phrasing wins over the
extractor's heuristic phrasing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rosclaw_know.schemas import (
    CandidatePattern,
    EvidenceBlock,
    FailureMode,
    PatternCardV2,
    Priority,
    SourceQualityLevel,
)

logger = logging.getLogger(__name__)


# ── task-family → (domain, embodiments, languages) defaults ───────────
#
# These line up with FAMILY_TO_DOMAIN in benchmark_extractor.py.  When
# the candidate carries no explicit context, the compiler falls back
# to these defaults so the resulting PatternCardV2 still validates.

_FAMILY_DEFAULTS: dict[str, dict[str, object]] = {
    "robotics_optimization": {
        "domain": "Control_Locomotion",
        "embodiment_types": ["uav", "manipulator", "wheeled_robot", "quadruped"],
        "artifact_languages": ["python", "cpp"],
    },
    "additive_manufacturing_optimization": {
        "domain": "Control_Locomotion",
        "embodiment_types": ["manipulator"],
        "artifact_languages": ["python"],
    },
    "astrodynamics_optimization": {
        "domain": "Control_Locomotion",
        "embodiment_types": ["uav"],
        "artifact_languages": ["python"],
    },
    "power_systems_optimization": {
        "domain": "Control_Locomotion",
        "embodiment_types": ["data_center"],
        "artifact_languages": ["python"],
    },
    "sustainable_data_center_control_optimization": {
        "domain": "Control_Locomotion",
        "embodiment_types": ["data_center"],
        "artifact_languages": ["python"],
    },
    "cryptographic_optimization": {
        "domain": "Systems_Compute",
        "embodiment_types": ["gpu_kernel"],
        "artifact_languages": ["cpp"],
    },
    "kernel_engineering_optimization": {
        "domain": "Systems_Compute",
        "embodiment_types": ["gpu_kernel"],
        "artifact_languages": ["python", "cuda", "triton"],
    },
    "computer_systems_optimization": {
        "domain": "Systems_Compute",
        "embodiment_types": ["gpu_kernel"],
        "artifact_languages": ["cpp"],
    },
    "single_cell_analysis_optimization": {
        "domain": "Learning_Training",
        "embodiment_types": [],
        "artifact_languages": ["python"],
    },
    "optics_optimization": {
        "domain": "World_Physics",
        "embodiment_types": ["optical_system"],
        "artifact_languages": ["python"],
    },
    "job_shop_optimization": {
        "domain": "Planning_Decision",
        "embodiment_types": [],
        "artifact_languages": ["python"],
    },
    "inventory_optimization_optimization": {
        "domain": "Planning_Decision",
        "embodiment_types": [],
        "artifact_languages": ["python"],
    },
    "py_portfolio_opt_optimization": {
        "domain": "Planning_Decision",
        "embodiment_types": [],
        "artifact_languages": ["python"],
    },
    "eng_design_optimization": {
        "domain": "Planning_Decision",
        "embodiment_types": [],
        "artifact_languages": ["python"],
    },
    "unknown_optimization": {
        "domain": "Systems_Compute",
        "embodiment_types": [],
        "artifact_languages": ["python"],
    },
}


# Sections that MUST appear in the rendered markdown.  Sprint 4 linter
# enforces this list — see ``scripts/lint_pattern_v2.py``.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Symptom",
    "Diagnosis",
    "Preconditions",
    "Next Experiment",
    "Code Target",
    "Expected Verifier Signal",
    "Anti-pattern",
    "Contraindications",
)


# ── compile: CandidatePattern → PatternCardV2 ─────────────────────────


@dataclass
class CompileContext:
    """Optional side-channel context that improves the compiled output.

    All fields are optional — the compiler still produces a valid
    PatternCardV2 even when no context is supplied.  When present, the
    failure_modes catalog overrides heuristic symptom/diagnosis text.
    """

    failure_modes: dict[str, FailureMode] = field(default_factory=dict)
    """``failure_id → FailureMode`` lookup, e.g. parsed from
    ``data/assets/failure_taxonomy.yaml``."""

    task_card_lookup: dict[str, list[str]] = field(default_factory=dict)
    """``task_family → list of associated TaskCard ids``, e.g. derived
    from ``data/assets/task_cards.yaml``."""


def compile_pattern_card(
    candidate: CandidatePattern,
    *,
    context: CompileContext | None = None,
) -> PatternCardV2:
    """Build a PatternCardV2 from a Sprint-3 CandidatePattern.

    Strategy: prefer authoritative text from the failure_taxonomy when
    the candidate's ``failure_id`` matches; fall back to the
    candidate's own ``diagnosis`` field otherwise.
    """
    ctx = context or CompileContext()
    fam = candidate.task_family
    defaults = _FAMILY_DEFAULTS.get(fam, _FAMILY_DEFAULTS["unknown_optimization"])
    domain = defaults["domain"]  # type: ignore[assignment]

    # Symptom + diagnosis: prefer authoritative FailureMode text.
    fm = ctx.failure_modes.get(candidate.failure_id) if candidate.failure_id else None
    if fm is not None:
        symptom = fm.symptom_text
        diagnosis = _compose_diagnosis(fm, candidate)
    else:
        symptom = _symptom_from_candidate(candidate)
        diagnosis = candidate.diagnosis

    preconditions = _preconditions(candidate, fm)
    next_experiment = _next_experiment(candidate)
    code_target = _code_target(candidate)
    patch_sketch = _patch_sketch(candidate)
    expected_signals = _expected_signals(candidate)
    anti_patterns = _anti_patterns(candidate, fm)
    contraindications = list(candidate.contraindications)
    if fm is not None:
        for c in fm.contraindications:
            if c not in contraindications:
                contraindications.append(c)
    cross_domain_analogy = _cross_domain_analogy(candidate)

    # Evidence block: candidate carries n via evidence_count + delta.
    ev = EvidenceBlock(
        n=candidate.evidence_count,
        avg_uplift=float(candidate.avg_score_delta or 0.0),
        win_rate=0.0,
        hint_use_rate=0.0,
    )

    # Priority: candidates derived from baseline_archive evidence land
    # in staging.  Promotion logic (plan §6) requires real placebo-
    # adjusted uplift, which we don't have yet — keep them at 0.
    priority: Priority | None = 0

    # Source quality: candidates come from real LLM-agent runs against
    # real verifiers, which the plan §11.2 grades as A
    # ("paper + reproducible code" tier) for Sprint 4 — they're not
    # self-verified by ROSClaw (that'd be S), but they're better than
    # tutorial-grade.  When evidence_count is low we drop to B.
    source_quality: SourceQualityLevel = "A" if candidate.evidence_count >= 5 else "B"

    return PatternCardV2(
        id=_pattern_id_from_candidate(candidate),
        domain=domain,
        task_families=[fam],
        embodiment_types=list(defaults["embodiment_types"]),  # type: ignore[arg-type]
        artifact_languages=list(defaults["artifact_languages"]),  # type: ignore[arg-type]
        priority=priority,
        symptom=symptom,
        diagnosis=diagnosis,
        preconditions=preconditions,
        next_experiment=next_experiment,
        code_target=code_target,
        patch_sketch=patch_sketch,
        expected_verifier_signals=expected_signals,
        anti_patterns=anti_patterns,
        contraindications=contraindications,
        cross_domain_analogy=cross_domain_analogy,
        source_quality=source_quality,
        source_ids=list(candidate.source_trajectory_ids),
        evidence=ev,
    )


# ── per-section builders ──────────────────────────────────────────────


def _pattern_id_from_candidate(candidate: CandidatePattern) -> str:
    """``candidate_zero_integral_gain_on_saturation`` → ``compiled_zero_integral_gain_on_saturation``.

    The ``compiled_`` prefix marks Sprint-4 output; the rest preserves
    candidate semantics so cross-referencing is obvious.
    """
    body = candidate.id.removeprefix("candidate_")
    return f"compiled_{body}"


def _symptom_from_candidate(candidate: CandidatePattern) -> str:
    """Heuristic symptom phrasing when no FailureMode is attached."""
    return candidate.diagnosis.split(".")[0].strip() + "."


def _compose_diagnosis(fm: FailureMode, candidate: CandidatePattern) -> str:
    """Blend the FailureMode's likely_causes with the candidate's prose."""
    lines: list[str] = []
    if fm.likely_causes:
        lines.append(
            "Likely cause(s):\n"
            + "\n".join(f"- {c}" for c in fm.likely_causes)
        )
    lines.append(candidate.diagnosis.strip())
    return "\n\n".join(lines)


def _preconditions(
    candidate: CandidatePattern, fm: FailureMode | None,
) -> list[str]:
    """Preconditions: distilled from FailureMode observable_signals if
    present, else derived from the successful mutations' target
    identifiers."""
    pre: list[str] = []
    if fm is not None and fm.observable_signals:
        for sig in fm.observable_signals:
            pre.append(f"Observable signal: {sig}")
    # From mutation targets
    targets = sorted({
        m.target_identifier for m in candidate.successful_mutations
        if m.target_identifier
    })
    if targets:
        pre.append(
            "Symbols present in the editable artifact: "
            + ", ".join(f"`{t}`" for t in targets[:6])
        )
    if not pre:
        pre.append(
            "The candidate artifact compiles and runs without errors "
            "on the baseline evaluator."
        )
    return pre


def _next_experiment(candidate: CandidatePattern) -> str:
    """Action template — read by the agent as 'do this next'.

    Built from the successful mutations: each mutation becomes one
    bullet, grouped by ``MutationKind``.  Phrased imperatively.
    """
    if not candidate.successful_mutations:
        return (
            "Reproduce the candidate's setup, then sweep one parameter "
            "at a time to find the highest-impact lever."
        )

    by_kind: dict[str, list[str]] = {}
    for m in candidate.successful_mutations:
        by_kind.setdefault(m.kind, []).append(m.description)

    bullets: list[str] = []
    for kind, descs in by_kind.items():
        action = _imperative_for_kind(kind)
        if action is None:
            for d in descs:
                bullets.append(f"- {d.capitalize()}.")
        else:
            joined = "; ".join(_drop_period(d) for d in descs)
            bullets.append(f"- {action}: {joined}.")
    return "\n".join(bullets)


def _imperative_for_kind(kind: str) -> str | None:
    """Map MutationKind → imperative verb phrase."""
    return {
        "set_parameter_zero":      "Set the named parameter(s) to zero",
        "set_parameter_constant":  "Pin the named parameter(s) to a literal",
        "add_output_clamp":        "Clamp the controller output before it leaves the function",
        "add_time_budget":         "Gate the search loop on a wall-clock deadline",
        "add_input_validation":    "Add a finiteness / range check at the boundary",
        "remove_assertion":        "Remove debug-only assertions",
        "swap_optimizer":          "Swap the search loop to a structured optimiser",
        "vectorize_loop":          "Replace the inner Python loop with numpy / array operations",
        "cache_repeated_call":     "Memoise the repeated expensive call",
        "switch_algorithm_class":  "Swap the algorithm family",
        "raise_iteration_count":   "Raise the iteration count",
        "lower_iteration_count":   "Lower the iteration count",
        "add_initialization_seed": "Seed the optimiser from a prior-best solution",
        "other":                   None,
    }.get(kind)


def _drop_period(s: str) -> str:
    return s.rstrip().rstrip(".")


def _code_target(candidate: CandidatePattern) -> str:
    """Tell the agent *where* to look in their artifact."""
    targets = sorted({
        m.target_identifier for m in candidate.successful_mutations
        if m.target_identifier
    })
    if targets:
        return (
            "Search the editable artifact for these identifiers and "
            "treat their definitions as the patch site: "
            + ", ".join(f"`{t}`" for t in targets)
            + "."
        )
    kinds = sorted({m.kind for m in candidate.successful_mutations})
    if "swap_optimizer" in kinds or "switch_algorithm_class" in kinds:
        return (
            "Locate the main search loop (typically a function whose name "
            "contains `optimize` / `search` / `solve`) and treat its body "
            "as the patch site."
        )
    if "add_time_budget" in kinds:
        return (
            "Locate every `while` / `for` loop that controls the search "
            "iteration budget; treat its guard expression as the patch "
            "site."
        )
    return "The whole editable artifact is the patch site."


def _patch_sketch(candidate: CandidatePattern) -> str:
    """A *recipe* — never a verbatim solution.

    We give the agent the *shape* of the change (with names blanked
    out where appropriate) instead of pasting any baseline_archive
    code.  Plan §3.5 explicitly forbids verbatim snippets here.
    """
    by_kind = sorted({m.kind for m in candidate.successful_mutations})
    lines: list[str] = []
    if "set_parameter_zero" in by_kind:
        lines.append("```python")
        lines.append("# Zero out the named parameter(s) in your candidate dict.")
        lines.append("# Example shape (replace identifier with the one in your code):")
        lines.append("best_gains[\"<param>\"] = 0.0   # was nonzero in baseline")
        lines.append("```")
    if "add_output_clamp" in by_kind:
        lines.append("```python")
        lines.append("# Clamp the controller output BEFORE the actuator-lag filter.")
        lines.append("out = max(min_value, min(max_value, out))")
        lines.append("# or numpy:")
        lines.append("out = np.clip(out, -bound, bound)")
        lines.append("```")
    if "add_time_budget" in by_kind:
        lines.append("```python")
        lines.append("import time")
        lines.append("DEADLINE = time.time() + WALLCLOCK_BUDGET")
        lines.append("while time.time() < DEADLINE:")
        lines.append("    ...   # search step")
        lines.append("```")
    if "swap_optimizer" in by_kind:
        lines.append("```python")
        lines.append("# Replace `random_search()` with a structured optimiser.")
        lines.append("# Acceptable choices: CMA-ES, Bayesian, differential evolution.")
        lines.append("# Pair with a wall-clock deadline (see add_time_budget).")
        lines.append("```")
    if "vectorize_loop" in by_kind:
        lines.append("```python")
        lines.append("# Replace explicit `for` over candidates with numpy array form.")
        lines.append("x_vec = np.asarray(x_list)")
        lines.append("scores = f_vectorised(x_vec)   # one call, vectorised body")
        lines.append("```")
    if "add_input_validation" in by_kind:
        lines.append("```python")
        lines.append("# Finiteness + range guard at the function boundary.")
        lines.append("if not np.all(np.isfinite(out)):")
        lines.append("    raise ValueError(\"non-finite output\")")
        lines.append("out = np.clip(out, lo, hi)")
        lines.append("```")
    if "add_initialization_seed" in by_kind:
        lines.append("```python")
        lines.append("# Warm-start from a prior-best solution on the SAME task.")
        lines.append("# DO NOT embed concrete tuning values from another task's archive.")
        lines.append("initial = load_prior_best_for_this_task()")
        lines.append("optimiser = make_optimizer(initial=initial)")
        lines.append("```")
    if not lines:
        lines.append("_(no patch shape inferred — see Next Experiment)_")
    return "\n".join(lines)


def _expected_signals(candidate: CandidatePattern) -> list[str]:
    """Single-signal field from the candidate becomes a list."""
    if candidate.expected_verifier_signal:
        return [candidate.expected_verifier_signal]
    return ["score improves; evaluator feasibility stays valid"]


def _anti_patterns(
    candidate: CandidatePattern, fm: FailureMode | None,
) -> list[str]:
    """Free-form anti-pattern statements.

    Sourced from the FailureMode's contraindications when available
    (they read as "do not …" prose).  Falls back to a generic
    placeholder so the section is never empty.
    """
    out: list[str] = []
    if fm is not None:
        for c in fm.contraindications:
            out.append(c)
    if not out:
        out.append(
            "Do not embed concrete tuning values from another task's "
            "baseline archive — see plan §3.5."
        )
    return out


def _cross_domain_analogy(candidate: CandidatePattern) -> str:
    """Tiny cross-domain hint.  Sprint 5 graph builder will replace
    this with real analogies; for now we hand-wire the most common
    ones."""
    kinds = {m.kind for m in candidate.successful_mutations}
    if "set_parameter_zero" in kinds and "robotics" in candidate.task_family:
        return (
            "**Learning_Training**: same shape as `clip_grad_norm_` — "
            "an upper bound that only fires when the magnitude exceeds "
            "a known physical limit.  Both are 'stop the integration "
            "when downstream saturates'."
        )
    if "add_time_budget" in kinds:
        return (
            "**Systems_Compute**: same shape as a request timeout in "
            "an RPC client — gate the inner loop on a wall-clock "
            "deadline, fail soft when it elapses."
        )
    return ""


# ── markdown renderer ────────────────────────────────────────────────


def render_markdown(card: PatternCardV2) -> str:
    """Render a PatternCardV2 to Sprint-4-required markdown.

    Section ordering follows plan §11.6 acceptance list, and every
    REQUIRED_SECTIONS heading appears verbatim so the linter can
    spot omissions with a one-line regex.
    """
    parts: list[str] = []

    # ── frontmatter ──────────────────────────────────────────────
    parts.append("---")
    parts.append(f"pattern_id: {card.id}")
    parts.append(f"schema_version: \"{card.schema_version}\"")
    parts.append(f"domain: {card.domain}")
    parts.append(f"task_families: {list(card.task_families)}")
    parts.append(f"embodiment_types: {list(card.embodiment_types)}")
    parts.append(f"artifact_languages: {list(card.artifact_languages)}")
    parts.append(
        f"priority: {card.priority if card.priority is not None else 'null'}"
    )
    parts.append(f"source_quality: {card.source_quality}")
    if card.source_ids:
        parts.append("source_ids:")
        for s in card.source_ids[:8]:
            parts.append(f"  - {s}")
        if len(card.source_ids) > 8:
            parts.append(f"  # …and {len(card.source_ids) - 8} more (truncated)")
    parts.append("evidence:")
    parts.append(f"  n: {card.evidence.n}")
    parts.append(f"  avg_uplift: {card.evidence.avg_uplift:.4f}")
    parts.append(f"  win_rate: {card.evidence.win_rate:.4f}")
    parts.append(f"  hint_use_rate: {card.evidence.hint_use_rate:.4f}")
    parts.append("---")
    parts.append("")

    # ── title ────────────────────────────────────────────────────
    title = card.symptom.rstrip(".")
    parts.append(f"# {title}")
    parts.append("")

    # ── Symptom ──────────────────────────────────────────────────
    parts.append("## Symptom")
    parts.append("")
    parts.append(card.symptom)
    parts.append("")

    # ── Diagnosis ────────────────────────────────────────────────
    parts.append("## Diagnosis")
    parts.append("")
    parts.append(card.diagnosis)
    parts.append("")

    # ── Preconditions ────────────────────────────────────────────
    parts.append("## Preconditions")
    parts.append("")
    if card.preconditions:
        for p in card.preconditions:
            parts.append(f"- {p}")
    else:
        parts.append("_(none beyond a runnable baseline)_")
    parts.append("")

    # ── Next Experiment ──────────────────────────────────────────
    parts.append("## Next Experiment")
    parts.append("")
    parts.append(card.next_experiment)
    parts.append("")

    # ── Code Target ──────────────────────────────────────────────
    parts.append("## Code Target")
    parts.append("")
    parts.append(card.code_target)
    parts.append("")

    # ── Patch Sketch ─────────────────────────────────────────────
    parts.append("## Patch Sketch")
    parts.append("")
    parts.append(card.patch_sketch or "_(no patch shape inferred — see Next Experiment)_")
    parts.append("")

    # ── Expected Verifier Signal ─────────────────────────────────
    parts.append("## Expected Verifier Signal")
    parts.append("")
    if card.expected_verifier_signals:
        for s in card.expected_verifier_signals:
            parts.append(f"- {s}")
    else:
        parts.append("- score improves; evaluator feasibility stays valid")
    parts.append("")

    # ── Anti-pattern ─────────────────────────────────────────────
    parts.append("## Anti-pattern")
    parts.append("")
    if card.anti_patterns:
        for a in card.anti_patterns:
            parts.append(f"- {a}")
    else:
        parts.append("- _(no anti-pattern documented yet)_")
    parts.append("")

    # ── Contraindications ────────────────────────────────────────
    parts.append("## Contraindications")
    parts.append("")
    if card.contraindications:
        for c in card.contraindications:
            parts.append(f"- {c}")
    else:
        parts.append("- _(no known contraindications)_")
    parts.append("")

    # ── Cross-domain analogy (optional) ──────────────────────────
    if card.cross_domain_analogy:
        parts.append("## Cross-domain analogy")
        parts.append("")
        parts.append(card.cross_domain_analogy)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


__all__ = [
    "REQUIRED_SECTIONS",
    "CompileContext",
    "compile_pattern_card",
    "render_markdown",
]
