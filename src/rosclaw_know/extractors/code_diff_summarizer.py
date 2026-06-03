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

from rosclaw_know.schemas import Mutation

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


# ── AES / cryptographic family detectors (Sprint 3 收尾) ──────────────────
#
# These run on every diff regardless of task name; the family
# extractors decide which CandidatePatterns to emit.  Keeping them
# generic in the summariser layer means a non-crypto task that
# happens to add a lookup table still gets classified correctly.

_AES_SBOX_HINT_RE = re.compile(
    r"\b(sbox|s_box|S_BOX|SBOX|t_box|T_box|TE0|TE1|TE2|TE3|"
    r"TD0|TD1|TD2|TD3|Rcon|RCON)\b"
)
_AES_TABLE_DECL_RE = re.compile(
    r"\b(static\s+const\s+(?:uint8_t|uint32_t)\s+\w*(?:sbox|t_box|Rcon)\w*"
    r"|const\s+\w+\s*=\s*\[\s*0x[0-9A-Fa-f]{1,2}\s*,)",
    re.IGNORECASE,
)


def _detect_lookup_table(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect new AES-style lookup-table additions (S-box, T-table, Rcon).

    Counts how many distinct table-name hits the candidate has vs the
    baseline.  We deliberately only emit the *kind of table* — never
    its bytes — to honour plan §3.5 (no leaked benchmark answers; an
    S-box leak would let an agent copy-paste a working AES).
    """
    base_hits = {
        m.group(0).lower()
        for m in _AES_SBOX_HINT_RE.finditer(baseline_src)
    }
    cand_hits = {
        m.group(0).lower()
        for m in _AES_SBOX_HINT_RE.finditer(candidate_src)
    }
    new_tables = sorted(cand_hits - base_hits)
    if not new_tables:
        return []
    return [
        Mutation(
            kind="add_lookup_table",
            description=f"added lookup table {name}",
            target_identifier=name,
        )
        for name in new_tables[:4]  # cap to keep cards readable
    ]


_UNROLL_PRAGMA_RE = re.compile(
    r"#\s*pragma\s+(unroll|GCC\s+unroll|clang\s+loop\s+unroll)",
    re.IGNORECASE,
)
# Repeated round-style blocks of the form "AddRoundKey(state, 0); SubBytes(state); …"
_AES_ROUND_CALL_RE = re.compile(
    r"\b(AddRoundKey|SubBytes|ShiftRows|MixColumns|aes_round|round_function)\b"
)


def _detect_unroll_loop(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect explicit ``#pragma unroll`` or repeated round-call blocks.

    Two signals are treated as positive:

    1. A pragma the baseline doesn't have.
    2. A jump in the count of AES round-call symbols (e.g. the
       baseline calls ``aes_round`` 1× inside a loop; the candidate
       writes it out 10× verbatim).  The threshold of 3 catches the
       "openevolve hard-coded the 10 rounds" idiom we see in real
       Frontier-Eng patches.
    """
    base_has_pragma = bool(_UNROLL_PRAGMA_RE.search(baseline_src))
    cand_has_pragma = bool(_UNROLL_PRAGMA_RE.search(candidate_src))
    base_round_calls = len(_AES_ROUND_CALL_RE.findall(baseline_src))
    cand_round_calls = len(_AES_ROUND_CALL_RE.findall(candidate_src))

    if cand_has_pragma and not base_has_pragma:
        return [Mutation(
            kind="unroll_loop",
            description="added explicit unroll pragma to inner loop",
        )]
    if cand_round_calls >= max(3, base_round_calls + 3):
        return [Mutation(
            kind="unroll_loop",
            description=(
                "manually unrolled round structure "
                "(symbol count rose above baseline)"
            ),
        )]
    return []


_BRANCHLESS_HINT_RE = re.compile(
    r"\b(cmov|xor[a-z]?\s*\?|__builtin_expect|select4u|"
    r"_mm_blendv|tl\.where|np\.where|np\.select)\b"
    r"|"
    r"\([^()\n]{0,40}\?\s*[^()\n]{0,30}:\s*[^()\n]{0,30}\)"  # ternary
    r"|"
    r"(?:uint8_t|uint16_t|uint32_t|uint64_t|int|int32_t|unsigned\s+\w+)?"
    r"\s*\bmask\s*=\s*[^;]*[<>]{1,2}",  # sign-extension mask trick
)


def _detect_branchless_select(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect constant-time / branchless select inserts."""
    base_hits = len(_BRANCHLESS_HINT_RE.findall(baseline_src))
    cand_hits = len(_BRANCHLESS_HINT_RE.findall(candidate_src))
    if cand_hits >= base_hits + 1:
        return [Mutation(
            kind="add_branchless_select",
            description=(
                "introduced a branchless / constant-time select "
                "in place of an if-branch"
            ),
        )]
    return []


_CONSTANT_TIME_COMPARE_RE = re.compile(
    r"\b(constant_time_compare|consttime_memequal|CRYPTO_memcmp|"
    r"hmac\.compare_digest)\b",
)


def _detect_constant_time_compare(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    if _CONSTANT_TIME_COMPARE_RE.search(baseline_src):
        return []
    if _CONSTANT_TIME_COMPARE_RE.search(candidate_src):
        return [Mutation(
            kind="add_constant_time_compare",
            description=(
                "replaced early-exit memcmp with a constant-time "
                "comparison helper"
            ),
        )]
    return []


# ── CUDA / Triton kernel detectors (Sprint 3 收尾) ──────────────────────


_SHARED_MEM_RE = re.compile(
    r"\b__shared__\b|\btl\.load\s*\(|\bshared_mem\b",
)
_TILE_RE = re.compile(
    r"\b(BLOCK_(?:M|N|K|D|H)|TILE_(?:M|N|K)|tl\.cdiv|"
    r"block_idx|threadIdx\.x|tile_id)\b",
)


def _detect_shared_memory_tile(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect ``__shared__`` buffer + tile-style load addition.

    Requires *both* shared-memory and tile constants to show up in the
    candidate but not the baseline — together they form the classic
    "promote inputs into shared mem before reuse" optimisation.
    """
    base_has = bool(_SHARED_MEM_RE.search(baseline_src)
                    and _TILE_RE.search(baseline_src))
    cand_has = bool(_SHARED_MEM_RE.search(candidate_src)
                    and _TILE_RE.search(candidate_src))
    if cand_has and not base_has:
        return [Mutation(
            kind="add_shared_memory_tile",
            description=(
                "promoted reused inputs into shared-memory tiles "
                "before the inner reduction"
            ),
        )]
    return []


_BLOCK_SIZE_RE = re.compile(
    r"\b(threadsPerBlock|BLOCK_SIZE|BLOCK_M|BLOCK_N|"
    r"num_warps|num_stages)\s*=\s*\d+",
)


def _detect_adjust_block_size(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect a *change* in block-size / num_warps / num_stages constant.

    We don't record the value, only the symbol — the magnitude itself
    would leak the tuned answer.
    """
    base_keys = {
        m.group(0).split("=")[0].strip()
        for m in _BLOCK_SIZE_RE.finditer(baseline_src)
    }
    cand_keys = {
        m.group(0).split("=")[0].strip()
        for m in _BLOCK_SIZE_RE.finditer(candidate_src)
    }
    new_keys = sorted(cand_keys - base_keys)
    if new_keys:
        return [Mutation(
            kind="adjust_block_size",
            description=f"introduced block-size constant {key}",
            target_identifier=key,
        ) for key in new_keys[:4]]
    # Same keys but different values — flag once.
    if base_keys & cand_keys:
        base_blob = "\n".join(
            m.group(0) for m in _BLOCK_SIZE_RE.finditer(baseline_src)
        )
        cand_blob = "\n".join(
            m.group(0) for m in _BLOCK_SIZE_RE.finditer(candidate_src)
        )
        if base_blob != cand_blob:
            return [Mutation(
                kind="adjust_block_size",
                description="tuned block-size / warp-count constants",
            )]
    return []


_KERNEL_DECL_RE = re.compile(
    r"\b(__global__\s+\w+\s+\w+|@triton\.jit|@triton\.autotune)",
)


def _detect_kernel_fusion(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect kernel-count drop (multiple kernels fused into one)."""
    base_kernels = len(_KERNEL_DECL_RE.findall(baseline_src))
    cand_kernels = len(_KERNEL_DECL_RE.findall(candidate_src))
    if base_kernels >= 2 and cand_kernels < base_kernels:
        return [Mutation(
            kind="add_kernel_fusion",
            description=(
                f"merged {base_kernels} kernel definitions down to "
                f"{cand_kernels} fused launch"
            ),
        )]
    return []


_WARP_SPEC_RE = re.compile(
    r"\b(producer_warp|consumer_warp|warp_specialize|tl\.warp_specialize|"
    r"cp\.async\.commit_group|cuda::pipeline)\b",
)


def _detect_warp_specialization(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    if _WARP_SPEC_RE.search(baseline_src):
        return []
    if _WARP_SPEC_RE.search(candidate_src):
        return [Mutation(
            kind="add_warp_specialization",
            description=(
                "split warps into producer / consumer roles for "
                "double-buffered load + compute"
            ),
        )]
    return []


_ASYNC_COPY_RE = re.compile(
    r"\b(cp\.async|tl\.async_copy|memcpy_async|"
    r"cuda::memcpy_async)\b",
)


def _detect_async_copy(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    if _ASYNC_COPY_RE.search(baseline_src):
        return []
    if _ASYNC_COPY_RE.search(candidate_src):
        return [Mutation(
            kind="add_async_copy",
            description=(
                "introduced asynchronous global→shared copies to "
                "overlap load with compute"
            ),
        )]
    return []


# ── Scheduling / dispatch detectors (Sprint 3 收尾) ──────────────────────


_SORT_KEY_RE = re.compile(
    r"\.sort\s*\(\s*key\s*="
    r"|sorted\s*\([^,)]+,\s*key\s*=",
)


def _detect_reorder_operations(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect addition of an explicit sort/reorder over jobs / operations."""
    base_hits = len(_SORT_KEY_RE.findall(baseline_src))
    cand_hits = len(_SORT_KEY_RE.findall(candidate_src))
    if cand_hits > base_hits:
        return [Mutation(
            kind="reorder_operations",
            description=(
                "added an explicit ordering pass over the job/operation "
                "list before scheduling"
            ),
        )]
    return []


_PRIORITY_KEY_RE = re.compile(
    r"\b("
    r"shortest[- _]?processing[- _]?time|SPT|"
    r"earliest[- _]?due[- _]?date|EDD|"
    r"longest[- _]?processing[- _]?time|LPT|"
    r"critical[- _]?ratio|CR_value|"
    r"slack[- _]?time|MS_value|"
    r"weighted[- _]?shortest|WSPT|"
    r"due_date|priority_key|priority_score"
    r")\b",
    re.IGNORECASE,
)


def _detect_priority_heuristic(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect the addition of a named scheduling priority heuristic."""
    base_hits = {m.group(1).lower() for m in _PRIORITY_KEY_RE.finditer(baseline_src)}
    cand_hits = {m.group(1).lower() for m in _PRIORITY_KEY_RE.finditer(candidate_src)}
    new_keys = sorted(cand_hits - base_hits)
    if not new_keys:
        return []
    return [Mutation(
        kind="add_priority_heuristic",
        description=f"added scheduling priority heuristic {key}",
        target_identifier=key,
    ) for key in new_keys[:3]]


_DISPATCH_RE = re.compile(
    r"\b("
    r"first[- _]?fit[- _]?decreasing|FFD|"
    r"best[- _]?fit|"
    r"backward[- _]?scheduling|"
    r"forward[- _]?scheduling|"
    r"dispatch[- _]?rule|select_next_op|next_eligible|"
    r"apply_johnson_rule|johnson[- _'s]?\s+rule|gantt"
    r")\b",
    re.IGNORECASE,
)


def _detect_dispatch_rule(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    base_hits = bool(_DISPATCH_RE.search(baseline_src))
    cand_hits = bool(_DISPATCH_RE.search(candidate_src))
    if cand_hits and not base_hits:
        match = _DISPATCH_RE.search(candidate_src)
        return [Mutation(
            kind="add_dispatch_rule",
            description=f"adopted dispatch rule {match.group(1) if match else ''}".strip(),
            target_identifier=(match.group(1).lower() if match else None),
        )]
    return []


_DEPENDENCY_RE = re.compile(
    r"\b("
    r"precedence|predecessors|successor_jobs|"
    r"resource_capacity|machine_available|earliest_start|"
    r"latest_finish|topological_sort"
    r")\b",
    re.IGNORECASE,
)


def _detect_dependency_constraint(
    baseline_src: str, candidate_src: str,
) -> list[Mutation]:
    """Detect explicit precedence / resource-capacity constraint enforcement."""
    base_hits = len(_DEPENDENCY_RE.findall(baseline_src))
    cand_hits = len(_DEPENDENCY_RE.findall(candidate_src))
    if cand_hits >= base_hits + 2:
        return [Mutation(
            kind="add_dependency_constraint",
            description=(
                "explicitly threaded precedence / resource-capacity "
                "constraints through the scheduler"
            ),
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
    # Sprint 3 收尾 — AES / crypto detectors
    _detect_lookup_table,
    _detect_unroll_loop,
    _detect_branchless_select,
    _detect_constant_time_compare,
    # Sprint 3 收尾 — CUDA / Triton kernel detectors
    _detect_shared_memory_tile,
    _detect_adjust_block_size,
    _detect_kernel_fusion,
    _detect_warp_specialization,
    _detect_async_copy,
    # Sprint 3 收尾 — scheduling / dispatch detectors
    _detect_reorder_operations,
    _detect_priority_heuristic,
    _detect_dispatch_rule,
    _detect_dependency_constraint,
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
