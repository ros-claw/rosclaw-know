"""Abstraction-preserving code diff summariser (Sprint 3, plan §3.5).

Given the baseline source and a candidate source (e.g. ``program.py``
from ``baseline_archive``), return a list of :class:`Mutation` objects
that capture *what kind* of change happened — not the literal post-edit
values.  The plan is explicit that downstream pattern cards must NOT
embed concrete benchmark answers, since doing so turns the knowledge
base into a leaderboard cheat sheet.

Concrete examples
-----------------

Acceptable mutation descriptions (returned by this module)::

    "set integral gain to zero on Ki_z"
    "added output clamp on T_cmd"
    "swapped optimizer from random search to CMA-ES style strategy"
    "added wall-clock time budget guard"

NOT acceptable (this module avoids producing)::

    "set Ki_z = 0.0142 and Kp_z = 21.36"   # leaks the answer
    "candidate = {'Kp_z': 23.0, 'Ki_z': 0.0, ...}"   # leaks the answer

Approach
--------

Pure-Python, no LLM:

1. Parse both sides into a normalised line set.
2. Compare with :mod:`difflib` to find inserted blocks.
3. Run a series of regex / AST detectors against the inserted blocks to
   classify each meaningful change into a :class:`MutationKind`.
4. Build the human-facing description from the kind + the *symbol*
   touched (e.g. ``Ki_z``), never from the symbol's value.

The detector tests in ``test_trajectory_extractor.py`` enforce that
the returned descriptions never contain bare numeric tokens.
"""
from __future__ import annotations

import ast
import difflib
import logging
import re
from dataclasses import dataclass

from rosclaw_know.schemas import Mutation, MutationKind

logger = logging.getLogger(__name__)


# Regex that catches a sequence of numeric tokens — used by the
# leak-detection guard at the end.  A *single* digit (e.g. "set to 0")
# is allowed because zero is a structural value, not a tunable answer.
_LEAK_RE = re.compile(r"(?<![A-Za-z_])-?\d+\.\d+(?![A-Za-z_])")


# ── detector helpers ──────────────────────────────────────────────────


def _find_assignment_targets(source: str) -> dict[str, str]:
    """Map ``target_name -> rhs_text`` for simple top-level assignments.

    Used by ``_detect_set_parameter_zero``.  Falls back gracefully when
    the source isn't valid Python (e.g. C++ files): empty dict, no
    crash.
    """
    out: dict[str, str] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    try:
                        out[tgt.id] = ast.unparse(node.value)
                    except Exception:
                        pass
    return out


def _dict_literal_keys_with_zero(source: str) -> set[str]:
    """Set of dict keys assigned to the literal ``0`` / ``0.0`` anywhere.

    Catches the most common Sprint 3 signal: an agent setting
    ``Ki_z: 0.0`` inside a dict literal as part of the anti-windup
    fix.  Returns the *key names* only, never the values.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                    continue
                if isinstance(v, ast.Constant) and v.value in (0, 0.0):
                    found.add(k.value)
                elif isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.USub):
                    # -0 — never seen in practice, but be defensive
                    if isinstance(v.operand, ast.Constant) and v.operand.value in (0, 0.0):
                        found.add(k.value)
    return found


# ── individual mutation detectors ─────────────────────────────────────


def _detect_set_parameter_zero(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Find dict-literal keys that flipped to zero in the candidate."""
    base_zeros = _dict_literal_keys_with_zero(baseline_src)
    cand_zeros = _dict_literal_keys_with_zero(candidate_src)
    newly_zero = cand_zeros - base_zeros
    return [
        Mutation(
            kind="set_parameter_zero",
            description=f"set parameter to zero on {name}",
            target_identifier=name,
        )
        for name in sorted(newly_zero)
    ]


def _detect_output_clamp(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Find ``np.clip`` / ``min(..., max_)`` insertions not present in baseline."""
    base_lines = set(baseline_src.splitlines())
    out: list[Mutation] = []
    seen_targets: set[str] = set()
    for line in candidate_src.splitlines():
        line = line.strip()
        if line in base_lines:
            continue
        for pat in (r"np\.clip\s*\(\s*([A-Za-z_][\w]*)",
                    r"max\s*\(\s*([A-Za-z_][\w]*)\s*,\s*-?[A-Za-z_]",
                    r"min\s*\(\s*([A-Za-z_][\w]*)\s*,\s*[A-Za-z_]"):
            m = re.search(pat, line)
            if m:
                target = m.group(1)
                if target not in seen_targets:
                    seen_targets.add(target)
                    out.append(Mutation(
                        kind="add_output_clamp",
                        description=f"added output clamp on {target}",
                        target_identifier=target,
                    ))
                break
    return out


def _detect_time_budget(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """``TIME_BUDGET`` / ``deadline`` / ``time.time()`` patterns not in baseline."""
    in_base = any(
        re.search(r"\b(time\.time|TIME_BUDGET|deadline)\b", line)
        for line in baseline_src.splitlines()
    )
    if in_base:
        return []
    for line in candidate_src.splitlines():
        if re.search(r"\bTIME_BUDGET\b|\bdeadline\b|time\.time\(\)", line):
            return [Mutation(
                kind="add_time_budget",
                description="added wall-clock time budget guard",
            )]
    return []


def _detect_optimizer_swap(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Random search → CMA-ES / Bayesian / evolution detection."""
    base_has_random = bool(re.search(r"random_gains|rng\.uniform", baseline_src))
    candidate_classes: list[str] = []
    if re.search(r"\bCMA[\s_-]?ES\b|cma\.evolution", candidate_src, re.IGNORECASE):
        candidate_classes.append("CMA-ES")
    if re.search(r"\bbayesian\b|skopt|gp_minimize|botorch", candidate_src, re.IGNORECASE):
        candidate_classes.append("Bayesian optimization")
    if re.search(r"\bdifferential evolution\b|scipy\.optimize\.differential_evolution",
                 candidate_src, re.IGNORECASE):
        candidate_classes.append("differential evolution")
    if re.search(r"\bnelder[- _]?mead\b", candidate_src, re.IGNORECASE):
        candidate_classes.append("Nelder-Mead")

    if base_has_random and candidate_classes:
        names = " / ".join(candidate_classes)
        return [Mutation(
            kind="swap_optimizer",
            description=f"swapped optimizer from random search to {names}-style strategy",
        )]
    return []


def _detect_vectorize_loop(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Lift to numpy array form: explicit Python loop → ``np.asarray``."""
    base_arr = baseline_src.count("np.asarray") + baseline_src.count("np.array")
    cand_arr = candidate_src.count("np.asarray") + candidate_src.count("np.array")
    if cand_arr > base_arr + 1:
        return [Mutation(
            kind="vectorize_loop",
            description="replaced explicit Python loop with numpy array form",
        )]
    return []


def _detect_input_validation(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detection of new boundary checks (``isfinite``, ``assert``, ``raise``)."""
    base_lines = set(baseline_src.splitlines())
    out: list[Mutation] = []
    for line in candidate_src.splitlines():
        s = line.strip()
        if s in base_lines or not s:
            continue
        if (
            re.search(r"\bnp\.isfinite\b|\bmath\.isnan\b|\bmath\.isinf\b", s)
            or re.search(r"^\s*assert\b", line)
            or re.search(r"^\s*raise\s+ValueError\b", line)
        ):
            out.append(Mutation(
                kind="add_input_validation",
                description="added boundary / finiteness check on intermediate value",
            ))
            break
    return out


def _detect_initialization_seed(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """An explicit "previous-best" / "prior-best" / "seeded" init block.

    This is a leak risk if the agent embedded specific gain values from
    prior runs.  We flag the *behaviour* (using a prior-best init) but
    leave the values out of the description.
    """
    base_has = any(
        re.search(r"prior[- _]best|previous[- _]best|warm.start|best.known", line, re.IGNORECASE)
        for line in baseline_src.splitlines()
    )
    if base_has:
        return []
    for line in candidate_src.splitlines():
        if re.search(r"prior[- _]best|previous[- _]best|warm.start|best.known|BEST EVER",
                     line, re.IGNORECASE):
            return [Mutation(
                kind="add_initialization_seed",
                description="seeded optimizer from a prior-best solution",
            )]
    return []


_ALL_DETECTORS = (
    _detect_set_parameter_zero,
    _detect_output_clamp,
    _detect_time_budget,
    _detect_optimizer_swap,
    _detect_vectorize_loop,
    _detect_input_validation,
    _detect_initialization_seed,
)


# ── public API ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DiffSummary:
    """Diff summary returned by :func:`summarize_diff`.

    Keeps the inserted-line count for downstream observability, separate
    from the (possibly tiny) list of classified mutations.
    """

    inserted_lines: int
    removed_lines: int
    mutations: list[Mutation]


def summarize_diff(baseline_src: str, candidate_src: str) -> DiffSummary:
    """Compare baseline and candidate; return classified mutations.

    The output is deterministic — given the same two inputs, the same
    Mutation list comes back (in detector-registration order, then
    alphabetical on ``target_identifier``).
    """
    mutations: list[Mutation] = []
    for detector in _ALL_DETECTORS:
        try:
            mutations.extend(detector(baseline_src, candidate_src))
        except Exception as exc:  # never let one detector kill the whole pass
            logger.warning("detector %s failed: %s", detector.__name__, exc)

    # Compute coarse diff sizes for the operator.
    matcher = difflib.SequenceMatcher(
        a=baseline_src.splitlines(),
        b=candidate_src.splitlines(),
        autojunk=False,
    )
    inserted = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            inserted += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            inserted += j2 - j1
            removed += i2 - i1

    return DiffSummary(
        inserted_lines=inserted,
        removed_lines=removed,
        mutations=_scrub_descriptions(mutations),
    )


def _scrub_descriptions(mutations: list[Mutation]) -> list[Mutation]:
    """Refuse to return a Mutation whose description contains a float
    literal — that would leak an answer.

    Replaces the offending Mutation with one whose description has the
    numeric token redacted.  Integers (commonly the literal zero) are
    left alone.
    """
    scrubbed: list[Mutation] = []
    for m in mutations:
        if _LEAK_RE.search(m.description):
            redacted = _LEAK_RE.sub("<value>", m.description)
            scrubbed.append(m.model_copy(update={"description": redacted}))
        else:
            scrubbed.append(m)
    return scrubbed


__all__ = ["DiffSummary", "summarize_diff"]
