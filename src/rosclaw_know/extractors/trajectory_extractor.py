"""Trajectory extractor (Sprint 3, plan §5.3, §11.4).

Given a :class:`Trajectory` (real or single-step from baseline_archive),
produce :class:`CandidatePattern` objects via the family-specific
feature extractors.

Sprint 3 ships the framework + one feature extractor (PID).  AES /
CUDA / scheduling extractors are scheduled as follow-up modules in
``extractors/feature_*.py``; they slot in via the same
``FeatureExtractor`` protocol.

Inputs
------

The canonical trajectory layout is::

    runs/<run-id>/
        iteration_000/code.py
        iteration_000/eval.json     {"score": 0.041, "valid": true}
        iteration_001/code.py
        iteration_001/eval.json
        ...

For the Frontier-Eng ``baseline_archive`` corpus we use a degenerate
one-step form: ``baseline_text → final_best_text`` is treated as a
single step with iteration=0.  See
:func:`from_baseline_archive_pair`.

Outputs
-------

A list of :class:`CandidatePattern` objects.  The pattern compiler
(Sprint 4) folds them into PatternCardV2 markdown.

Acceptance gates (plan §11.4)
-----------------------------

* Each candidate pattern has at least one successful mutation.
* No candidate description contains concrete numeric values from
  the baseline_archive (enforced by
  :func:`code_diff_summarizer._scrub_descriptions`).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Callable

from rosclaw_know.extractors.code_diff_summarizer import summarize_diff
from rosclaw_know.schemas import (
    CandidatePattern,
    Mutation,
    Trajectory,
    TrajectoryStep,
)

logger = logging.getLogger(__name__)


# A feature extractor takes the trajectory and returns zero or more
# CandidatePattern objects.  Family-specific extractors register
# themselves in ``ALL_FEATURE_EXTRACTORS`` below.

FeatureExtractor = Callable[[Trajectory], list[CandidatePattern]]


# ── PID feature extractor ─────────────────────────────────────────────


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def extract_pid_features(traj: Trajectory) -> list[CandidatePattern]:
    """PID family — detect anti-windup, output clamp, time-budget,
    and optimizer-swap patterns.

    Only fires for trajectories whose ``task_name`` looks PID-related.
    Each returned pattern lists the trajectory's mutations of the
    corresponding ``MutationKind``.  Plan §3.5 forbids embedding
    concrete values — :func:`summarize_diff` already scrubs them, and
    this extractor only quotes the (kind, target_identifier) pair.
    """
    if not _is_pid_like(traj):
        return []

    # Collapse all step mutations into a single bag, keyed by kind.
    by_kind: dict[str, list[Mutation]] = defaultdict(list)
    for step in traj.steps:
        for m in step.mutations:
            by_kind[m.kind].append(m)

    out: list[CandidatePattern] = []

    # Zero-integral / anti-windup
    zero_mutations = by_kind.get("set_parameter_zero", [])
    integral_zero = [m for m in zero_mutations
                     if (m.target_identifier or "").lower().startswith("ki_")]
    if integral_zero:
        out.append(CandidatePattern(
            id="candidate_zero_integral_gain_on_saturation",
            task_family="robotics_optimization",
            failure_id="failure_pid_integrator_windup",
            diagnosis=(
                "When an actuator output is at saturation, allowing the "
                "integral term to keep accumulating produces windup.  "
                "Successful PID-tuning runs disable the integral channel "
                "entirely for axes where the actuator is regularly "
                "saturated."
            ),
            successful_mutations=integral_zero,
            expected_verifier_signal=(
                "feasibility stays valid, overshoot decreases, settling "
                "time after setpoint changes goes down."
            ),
            contraindications=[
                "do not raise Ki to compensate — the integrator will "
                "wind up again on the next saturation event",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    # Output clamp / hard-cap
    clamps = by_kind.get("add_output_clamp", [])
    controller_clamps = [
        m for m in clamps
        if (m.target_identifier or "").startswith((
            "T_cmd", "tau_cmd", "thrust", "desired_pitch",
            "desired_roll", "v_cmd", "u_cmd", "torque",
        ))
    ]
    if controller_clamps:
        out.append(CandidatePattern(
            id="candidate_controller_output_clamp",
            task_family="robotics_optimization",
            failure_id="failure_actuator_clamp_missing",
            diagnosis=(
                "Successful runs explicitly clamp the controller command "
                "to the actuator's physical range before it leaves the "
                "control function.  Relying on downstream code to clip "
                "is a frequent source of saturation-induced instability."
            ),
            successful_mutations=controller_clamps,
            expected_verifier_signal=(
                "feasibility stays valid even on aggressive maneuvers; "
                "thrust / torque never exceeds its bound."
            ),
            contraindications=[
                "clamps must be applied *before* the actuator-lag filter, "
                "not after, or the command will spike at the next sample",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    # Optimizer swap (random → structured)
    swaps = by_kind.get("swap_optimizer", [])
    if swaps:
        out.append(CandidatePattern(
            id="candidate_swap_random_search_to_structured_optimizer",
            task_family="robotics_optimization",
            diagnosis=(
                "Random search saturates well below the achievable "
                "score on PID-tuning tasks within the iteration budget.  "
                "Top runs replace it with a structured optimizer "
                "(CMA-ES, Bayesian, differential evolution)."
            ),
            successful_mutations=swaps,
            expected_verifier_signal=(
                "score climbs monotonically past the random-search "
                "plateau before the time budget elapses."
            ),
            contraindications=[
                "do not run unbounded — pair with an explicit time "
                "budget so the search returns before the wall clock "
                "expires (see candidate_add_time_budget)",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    # Time-budget guard
    if by_kind.get("add_time_budget"):
        out.append(CandidatePattern(
            id="candidate_add_time_budget",
            task_family="robotics_optimization",
            diagnosis=(
                "A structured optimizer with no wall-clock guard can run "
                "past the evaluator timeout and lose the run entirely.  "
                "Successful entries gate their search on "
                "``time.time() < deadline``."
            ),
            successful_mutations=by_kind["add_time_budget"],
            expected_verifier_signal=(
                "evaluator returncode stays 0 even when the search "
                "explores deep alternatives."
            ),
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    return out


def _is_pid_like(traj: Trajectory) -> bool:
    """True if the trajectory targets a PID-tuning style task.

    Conservative — matches on task name keywords and the presence of
    PID-related mutation targets.
    """
    name = traj.task_name.lower()
    if "pid" in name or "tuning" in name:
        return True
    # Inferred: if any step touches a Ki_*/Kp_*/Kd_* identifier, treat
    # as PID-like.  Catches Robotics_PIDTuning slug shapes too.
    for step in traj.steps:
        for m in step.mutations:
            t = (m.target_identifier or "").lower()
            if t.startswith(("ki_", "kp_", "kd_")):
                return True
    return False


# ── generic extractors (cross-family) ─────────────────────────────────


def extract_systems_features(traj: Trajectory) -> list[CandidatePattern]:
    """Cross-family systems patterns: vectorisation, caching, time-budget.

    These fire regardless of task family — Optics, JobShop,
    KernelEngineering, Cryptographic all benefit from the same
    "stop running unbounded Python loops" lessons.
    """
    out: list[CandidatePattern] = []
    by_kind: dict[str, list[Mutation]] = defaultdict(list)
    for step in traj.steps:
        for m in step.mutations:
            by_kind[m.kind].append(m)

    fam = _infer_task_family(traj)

    if by_kind.get("vectorize_loop"):
        out.append(CandidatePattern(
            id="candidate_vectorize_inner_loop",
            task_family=fam,
            diagnosis=(
                "Top-scoring runs replace explicit Python iteration over "
                "candidate arrays with numpy / array-form operations.  The "
                "inner-loop call overhead dominates wall-clock budget on "
                "score-bounded benchmarks."
            ),
            successful_mutations=by_kind["vectorize_loop"],
            expected_verifier_signal=(
                "more candidates evaluated within the same time budget; "
                "score plateau pushed further."
            ),
            contraindications=[
                "do not vectorise if the inner step has data-dependent "
                "branches — branchless numpy ops will compute discarded "
                "work and may even regress.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_input_validation"):
        out.append(CandidatePattern(
            id="candidate_add_boundary_validation",
            task_family=fam,
            diagnosis=(
                "Successful runs insert finiteness / range checks at the "
                "boundary of their solver so a NaN or invalid output is "
                "caught before the evaluator returns 'infeasible'.  "
                "Catches a large fraction of soft failures in "
                "Frontier-Eng's pure-Python tasks."
            ),
            successful_mutations=by_kind["add_input_validation"],
            expected_verifier_signal=(
                "feasibility rate stays high even on novel candidate "
                "configurations."
            ),
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_time_budget") and fam != "robotics_optimization":
        # PID extractor handles robotics; emit a generic version for the
        # other families so the pattern compiler sees cross-family
        # evidence.
        out.append(CandidatePattern(
            id="candidate_generic_time_budget",
            task_family=fam,
            diagnosis=(
                "Structured optimisers can overrun the per-task wall "
                "clock if left ungated.  Top entries explicitly check "
                "``time.time() < deadline`` inside their search loop."
            ),
            successful_mutations=by_kind["add_time_budget"],
            expected_verifier_signal=(
                "evaluator returncode stays 0; no wall-clock-timeout "
                "infeasibility."
            ),
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    return out


def extract_optimizer_features(traj: Trajectory) -> list[CandidatePattern]:
    """Cross-family optimizer patterns: algorithm swaps, warm starts."""
    out: list[CandidatePattern] = []
    by_kind: dict[str, list[Mutation]] = defaultdict(list)
    for step in traj.steps:
        for m in step.mutations:
            by_kind[m.kind].append(m)

    fam = _infer_task_family(traj)

    if by_kind.get("add_initialization_seed"):
        out.append(CandidatePattern(
            id="candidate_warm_start_from_prior_best",
            task_family=fam,
            diagnosis=(
                "Top entries seed their optimiser from a prior-best "
                "solution (their own or a sibling task's) rather than "
                "starting from a random or hand-tuned baseline.  Cuts "
                "the path-length to a high score significantly."
            ),
            successful_mutations=by_kind["add_initialization_seed"],
            expected_verifier_signal=(
                "first valid score is already close to the search "
                "ceiling; subsequent iterations refine rather than "
                "discover."
            ),
            contraindications=[
                "never embed *concrete* gain values from another task "
                "verbatim — that turns the pattern into a leaderboard "
                "cheat sheet (see plan §3.5).",
                "the seed must come from a previous run on the *same* "
                "task; cross-task seeding is much less reliable.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("swap_optimizer") and fam != "robotics_optimization":
        # PID extractor already emits a robotics-specific version
        out.append(CandidatePattern(
            id="candidate_generic_swap_random_to_structured",
            task_family=fam,
            diagnosis=(
                "Random search is rarely competitive on Frontier-Eng's "
                "continuous-parameter benchmarks; top runs swap to a "
                "structured optimiser (CMA-ES / Bayesian / DE)."
            ),
            successful_mutations=by_kind["swap_optimizer"],
            expected_verifier_signal=(
                "convergence past the random-search plateau is reached "
                "well before the time budget elapses."
            ),
            contraindications=[
                "pair the structured search with a wall-clock budget — "
                "see candidate_generic_time_budget.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    return out


def _infer_task_family(traj: Trajectory) -> str:
    """Best-effort task family inference from trajectory metadata.

    Sprint 4's compiler reads this to bucket patterns into the right
    section of the bridge_index.  Returns
    ``"unknown_optimization"`` if nothing is conclusive.
    """
    n = traj.task_name.lower()
    if "pid" in n or "robot" in n:
        return "robotics_optimization"
    if "aes" in n or "sha" in n:
        return "cryptographic_optimization"
    if "flash" in n or "kernel" in n or "mla" in n:
        return "kernel_engineering_optimization"
    if "shop" in n:
        return "job_shop_optimization"
    if "inventory" in n:
        return "inventory_optimization_optimization"
    if "optics" in n or "fiber" in n or "holographic" in n or "phase" in n:
        return "optics_optimization"
    if "battery" in n:
        return "energy_storage_optimization"
    return "unknown_optimization"


# ── extractor registry ───────────────────────────────────────────────


ALL_FEATURE_EXTRACTORS: tuple[FeatureExtractor, ...] = (
    extract_pid_features,
    extract_systems_features,
    extract_optimizer_features,
)


def extract_candidate_patterns(traj: Trajectory) -> list[CandidatePattern]:
    """Run every registered feature extractor against ``traj``."""
    out: list[CandidatePattern] = []
    for fx in ALL_FEATURE_EXTRACTORS:
        try:
            out.extend(fx(traj))
        except Exception as exc:
            logger.warning("feature extractor %s failed: %s", fx.__name__, exc)
    return out


# ── baseline_archive driver ──────────────────────────────────────────


def from_baseline_archive_pair(
    *,
    baseline_text: str,
    candidate_text: str,
    task_name: str,
    trajectory_id: str,
    benchmark: str | None = "frontier-eng",
    algorithm: str | None = None,
    model: str | None = None,
    best_delta: float | None = None,
) -> Trajectory:
    """Build a one-step Trajectory from a baseline → final-best pair.

    Used for Sprint 3's bootstrap: the baseline_archive directory tree
    only contains final-best programs, not iteration history, so we
    fold the entire optimisation into a single TrajectoryStep with
    iteration=0.
    """
    summary = summarize_diff(baseline_text, candidate_text)
    step = TrajectoryStep(
        iteration=0,
        score=None,
        valid=True,
        mutations=summary.mutations,
    )
    return Trajectory(
        trajectory_id=trajectory_id,
        task_name=task_name,
        benchmark=benchmark,
        algorithm=algorithm,
        model=model,
        steps=[step],
        best_delta=best_delta,
        notes=[
            "single-step trajectory built from baseline → final-best "
            "(baseline_archive); no intermediate iterations available",
        ],
    )


def from_iteration_dir(run_dir: Path, task_name: str) -> Trajectory | None:
    """Build a Trajectory from an ``iteration_NNN/`` layout (the
    canonical run format).

    Returns ``None`` if no ``iteration_*`` subdirs are present.  Each
    iteration's mutations are computed against the *previous*
    iteration's code (or the first iteration's ``baseline.py`` if
    present, else empty source).
    """
    iters = sorted(run_dir.glob("iteration_*"))
    if not iters:
        return None

    baseline_path = run_dir / "baseline.py"
    prev_code = baseline_path.read_text(encoding="utf-8") if baseline_path.is_file() else ""

    steps: list[TrajectoryStep] = []
    import json
    for i, it in enumerate(iters):
        code_file = it / "code.py"
        eval_file = it / "eval.json"
        if not code_file.is_file():
            continue
        code = code_file.read_text(encoding="utf-8")
        eval_data: dict = {}
        if eval_file.is_file():
            try:
                eval_data = json.loads(eval_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        summary = summarize_diff(prev_code, code)
        steps.append(TrajectoryStep(
            iteration=i,
            score=eval_data.get("score"),
            valid=bool(eval_data.get("valid", True)),
            mutations=summary.mutations,
        ))
        prev_code = code

    # best_delta: last valid score minus first valid score
    valid_scores = [s.score for s in steps if s.score is not None]
    best_delta = (max(valid_scores) - valid_scores[0]) if len(valid_scores) >= 2 else None

    return Trajectory(
        trajectory_id=run_dir.name,
        task_name=task_name,
        steps=steps,
        best_delta=best_delta,
    )


__all__ = [
    "ALL_FEATURE_EXTRACTORS",
    "extract_candidate_patterns",
    "extract_pid_features",
    "from_baseline_archive_pair",
    "from_iteration_dir",
]
