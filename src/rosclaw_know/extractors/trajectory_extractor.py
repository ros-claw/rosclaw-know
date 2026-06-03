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
from collections.abc import Callable
from pathlib import Path

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


# ── AES / cryptographic family extractor (Sprint 3 收尾) ──────────────────


def extract_aes_features(traj: Trajectory) -> list[CandidatePattern]:
    """AES / crypto family — table lookup, unrolling, branchless, const-time compare.

    Fires for any trajectory whose task name mentions AES, SHA, crypto,
    OR whose mutation list contains AES-specific kinds (so a wrong
    task-name slug doesn't suppress otherwise-valid evidence).
    """
    if not _is_aes_like(traj):
        return []

    by_kind: dict[str, list[Mutation]] = defaultdict(list)
    for step in traj.steps:
        for m in step.mutations:
            by_kind[m.kind].append(m)

    out: list[CandidatePattern] = []

    if by_kind.get("add_lookup_table"):
        out.append(CandidatePattern(
            id="candidate_aes_use_precomputed_tables",
            task_family="cryptographic_optimization",
            failure_id="failure_aes_byte_op_overhead",
            diagnosis=(
                "Per-byte AES (sbox lookup → shift rows → mix columns "
                "in separate stages) is dominated by byte-level "
                "arithmetic overhead.  Top runs precompute T-tables "
                "(or merge sbox+mixcol into one 32-bit lookup) so each "
                "round becomes four XORed table loads."
            ),
            successful_mutations=by_kind["add_lookup_table"],
            expected_verifier_signal=(
                "throughput rises; verifier reports correct ciphertext "
                "for every test vector at higher MB/s."
            ),
            contraindications=[
                "do not embed the actual sbox bytes into the pattern — "
                "ship the *structural* recommendation (\"add a 256-entry "
                "lookup table indexed by state byte\"), not the bytes "
                "themselves (plan §3.5).",
                "T-tables increase code size and instruction cache "
                "pressure — verify on the target machine before adopting.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("unroll_loop"):
        out.append(CandidatePattern(
            id="candidate_aes_unroll_round_structure",
            task_family="cryptographic_optimization",
            failure_id="failure_aes_branch_mispredict",
            diagnosis=(
                "Looping over the 10 AES rounds keeps a loop counter "
                "live in a register and exposes a predictable but "
                "non-free branch.  Top entries hard-unroll the round "
                "schedule (or add ``#pragma unroll``) so the rounds "
                "fuse with surrounding arithmetic."
            ),
            successful_mutations=by_kind["unroll_loop"],
            expected_verifier_signal=(
                "throughput per block goes up without losing "
                "correctness on test vectors."
            ),
            contraindications=[
                "manual unrolling can blow the I-cache on smaller cores; "
                "prefer the pragma form when available.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_branchless_select"):
        out.append(CandidatePattern(
            id="candidate_aes_branchless_select",
            task_family="cryptographic_optimization",
            failure_id="failure_aes_timing_side_channel",
            diagnosis=(
                "Conditional moves and ``a if cond else b`` ternaries "
                "leak whether the secret key bit was 0 or 1 via "
                "branch-predictor state.  Successful runs convert "
                "secret-dependent branches into bitmask-driven "
                "branchless selects."
            ),
            successful_mutations=by_kind["add_branchless_select"],
            expected_verifier_signal=(
                "timing-side-channel test in the verifier (when "
                "present) stops flagging correlated wall-clock per key bit."
            ),
            contraindications=[
                "branchless selects on every comparison can hurt "
                "throughput on hot paths that are *not* secret-"
                "dependent — apply selectively.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_constant_time_compare"):
        out.append(CandidatePattern(
            id="candidate_aes_constant_time_compare",
            task_family="cryptographic_optimization",
            failure_id="failure_aes_early_exit_compare",
            diagnosis=(
                "Default ``memcmp``-style equality stops at the first "
                "mismatching byte; that lets an attacker recover MAC "
                "tags one byte at a time.  Successful runs swap to a "
                "fixed-time comparison routine that XORs every byte."
            ),
            successful_mutations=by_kind["add_constant_time_compare"],
            expected_verifier_signal=(
                "verifier's tag-validation tests still pass; timing "
                "test (if any) reports flat wall-clock independent of "
                "mismatch position."
            ),
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    return out


def _is_aes_like(traj: Trajectory) -> bool:
    name = traj.task_name.lower()
    if any(tok in name for tok in ("aes", "sha", "crypto", "cipher", "mac")):
        return True
    for step in traj.steps:
        for m in step.mutations:
            if m.kind in (
                "add_lookup_table",
                "unroll_loop",
                "add_branchless_select",
                "add_constant_time_compare",
            ):
                return True
    return False


# ── CUDA / Triton kernel extractor (Sprint 3 收尾) ──────────────────────


def extract_cuda_features(traj: Trajectory) -> list[CandidatePattern]:
    """CUDA / Triton kernel family — shared-mem tiling, block tuning, fusion, async.

    Fires for any trajectory whose task name mentions flash / kernel /
    triton / cuda / mla, OR whose mutations include kernel-specific kinds.
    """
    if not _is_cuda_like(traj):
        return []

    by_kind: dict[str, list[Mutation]] = defaultdict(list)
    for step in traj.steps:
        for m in step.mutations:
            by_kind[m.kind].append(m)

    out: list[CandidatePattern] = []

    if by_kind.get("add_shared_memory_tile"):
        out.append(CandidatePattern(
            id="candidate_cuda_shared_memory_tiling",
            task_family="kernel_engineering_optimization",
            failure_id="failure_cuda_global_memory_bound",
            diagnosis=(
                "When the same input element is read by multiple "
                "threads (e.g. K in attention, A row in matmul), "
                "leaving it in global memory dominates the kernel time. "
                " Top entries stage the reused tiles into shared memory "
                "(or Triton's ``tl.load`` + tile loop) before the inner "
                "reduction."
            ),
            successful_mutations=by_kind["add_shared_memory_tile"],
            expected_verifier_signal=(
                "tokens-per-sec or GB/s climbs; correctness vs reference "
                "kernel stays within tolerance."
            ),
            contraindications=[
                "shared memory is a scarce resource — over-tiling forces "
                "low occupancy and may regress.  Tile size must respect "
                "``cudaDeviceGetAttribute(maxSharedMemoryPerBlock)``.",
                "tile boundaries must guard against masked / partial "
                "loads or you'll silently read garbage on the edge.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("adjust_block_size"):
        out.append(CandidatePattern(
            id="candidate_cuda_tune_block_size",
            task_family="kernel_engineering_optimization",
            failure_id="failure_cuda_occupancy_too_low",
            diagnosis=(
                "Default block sizes are conservative.  Top entries "
                "expose BLOCK_M / BLOCK_N / num_warps / num_stages as "
                "tunable constants and pick values that maximise "
                "occupancy while keeping shared-memory + register "
                "footprint within budget."
            ),
            successful_mutations=by_kind["adjust_block_size"],
            expected_verifier_signal=(
                "throughput rises on the target device; reduced number "
                "of register-spill warnings."
            ),
            contraindications=[
                "do not hard-code the tuned values — keep them as named "
                "constants so an agent on a different SM count can "
                "re-tune.  Plan §3.5: ship the *symbol*, not the answer.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_kernel_fusion"):
        out.append(CandidatePattern(
            id="candidate_cuda_fuse_kernel_launches",
            task_family="kernel_engineering_optimization",
            failure_id="failure_cuda_launch_overhead",
            diagnosis=(
                "Two kernels chained back-to-back (e.g. softmax + matmul) "
                "pay double launch cost and round-trip the activation "
                "through global memory.  Successful entries fuse them "
                "into a single kernel so the activation stays in "
                "registers / shared mem between the stages."
            ),
            successful_mutations=by_kind["add_kernel_fusion"],
            expected_verifier_signal=(
                "wall-clock drops noticeably on smaller batch sizes "
                "(where launch overhead dominates) without correctness "
                "regressions."
            ),
            contraindications=[
                "fusion increases register pressure — beware of "
                "occupancy drop if the fused kernel spills.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_warp_specialization"):
        out.append(CandidatePattern(
            id="candidate_cuda_warp_specialization",
            task_family="kernel_engineering_optimization",
            failure_id="failure_cuda_load_compute_serialization",
            diagnosis=(
                "On Hopper-class GPUs, splitting warps into producer "
                "(``cp.async`` loaders) and consumer (compute) roles "
                "lets the next tile load overlap with the current "
                "tile's MMA work, doubling effective tensor-core "
                "utilisation."
            ),
            successful_mutations=by_kind["add_warp_specialization"],
            expected_verifier_signal=(
                "throughput climbs on Hopper / Ampere targets; "
                "tensor-core SOL grows in Nsight."
            ),
            contraindications=[
                "older architectures (sm < 80) cannot benefit and may "
                "regress because of the extra synchronisation.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_async_copy"):
        out.append(CandidatePattern(
            id="candidate_cuda_async_global_to_shared_copy",
            task_family="kernel_engineering_optimization",
            failure_id="failure_cuda_blocking_load",
            diagnosis=(
                "Loading inputs with a synchronous ``ldg`` / "
                "``__ldcs`` stall-the-warp pattern leaves the compute "
                "units idle.  Successful runs schedule the next tile's "
                "load via ``cp.async`` / ``tl.async_copy`` so the "
                "current tile's MMAs run concurrently."
            ),
            successful_mutations=by_kind["add_async_copy"],
            expected_verifier_signal=(
                "Nsight reports the load stage and compute stage "
                "overlapping; total kernel time drops."
            ),
            contraindications=[
                "async copies require careful ``cp.async.wait_group`` "
                "fences — missing fences are a correctness bug, not a "
                "perf bug.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    return out


def _is_cuda_like(traj: Trajectory) -> bool:
    name = traj.task_name.lower()
    if any(tok in name for tok in (
        "flash", "kernel", "triton", "cuda", "mla", "trimul", "mma",
        "matmul", "attention", "gemm",
    )):
        return True
    for step in traj.steps:
        for m in step.mutations:
            if m.kind in (
                "add_shared_memory_tile",
                "adjust_block_size",
                "add_kernel_fusion",
                "add_warp_specialization",
                "add_async_copy",
            ):
                return True
    return False


# ── Scheduling family extractor (Sprint 3 收尾) ──────────────────────


def extract_scheduling_features(traj: Trajectory) -> list[CandidatePattern]:
    """Scheduling / dispatch family — reorder, priority heuristic, dispatch, deps.

    Fires for any trajectory whose task name mentions shop / inventory /
    scheduling / dispatch / dc (data center) / power, OR whose
    mutations carry scheduling-specific kinds.
    """
    if not _is_scheduling_like(traj):
        return []

    by_kind: dict[str, list[Mutation]] = defaultdict(list)
    for step in traj.steps:
        for m in step.mutations:
            by_kind[m.kind].append(m)

    out: list[CandidatePattern] = []

    if by_kind.get("reorder_operations"):
        out.append(CandidatePattern(
            id="candidate_sched_explicit_operation_ordering",
            task_family="job_shop_optimization",
            failure_id="failure_sched_arrival_order_makespan",
            diagnosis=(
                "Many jobshop baselines schedule operations in their "
                "input order, which leaves a lot of slack on the table. "
                " Top entries explicitly sort the operation list by a "
                "priority key (SPT, EDD, slack-based) before dispatch."
            ),
            successful_mutations=by_kind["reorder_operations"],
            expected_verifier_signal=(
                "makespan drops; verifier reports lower bound met more "
                "often."
            ),
            contraindications=[
                "static priority rules struggle on instances with high "
                "resource contention — keep the rule swappable.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_priority_heuristic"):
        out.append(CandidatePattern(
            id="candidate_sched_priority_heuristic",
            task_family="job_shop_optimization",
            failure_id="failure_sched_no_priority_rule",
            diagnosis=(
                "Adding a named priority key (SPT / EDD / LPT / slack / "
                "critical-ratio) turns ad-hoc dispatch into a "
                "principled selection that survives larger instances."
            ),
            successful_mutations=by_kind["add_priority_heuristic"],
            expected_verifier_signal=(
                "makespan / tardiness reduction; ablation shows the "
                "named key beats the unsorted baseline."
            ),
            contraindications=[
                "do not embed *specific weight* values in the pattern — "
                "the *kind* of priority (SPT vs slack) is the lesson, "
                "the coefficients vary per instance.",
            ],
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_dispatch_rule"):
        out.append(CandidatePattern(
            id="candidate_sched_named_dispatch_rule",
            task_family="job_shop_optimization",
            failure_id="failure_sched_greedy_dispatch_bias",
            diagnosis=(
                "Switching to a *named* dispatch rule (Johnson's rule, "
                "first-fit-decreasing, backward scheduling) gives the "
                "scheduler optimisation hooks (e.g. early ready-list "
                "compaction) that anonymous dispatch loops lack."
            ),
            successful_mutations=by_kind["add_dispatch_rule"],
            expected_verifier_signal=(
                "improved utilisation; same instance solved in fewer "
                "dispatch decisions."
            ),
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    if by_kind.get("add_dependency_constraint"):
        out.append(CandidatePattern(
            id="candidate_sched_explicit_dependency_constraints",
            task_family="job_shop_optimization",
            failure_id="failure_sched_dependency_violations",
            diagnosis=(
                "Schedulers that treat precedence / resource caps as "
                "post-hoc checks produce infeasible schedules they "
                "then have to repair.  Top entries thread the "
                "constraints through the dispatch loop so every "
                "candidate operation is feasibility-clean before it "
                "lands on a machine."
            ),
            successful_mutations=by_kind["add_dependency_constraint"],
            expected_verifier_signal=(
                "validity_preservation_rate stays high even on dense "
                "instances; fewer repair iterations needed."
            ),
            evidence_count=1,
            avg_score_delta=traj.best_delta,
            source_trajectory_ids=[traj.trajectory_id],
        ))

    return out


def _is_scheduling_like(traj: Trajectory) -> bool:
    name = traj.task_name.lower()
    if any(tok in name for tok in (
        "shop", "inventory", "schedul", "dispatch", "data_center",
        "datacenter", "power", "energy_market", "abz", "swv",
    )):
        return True
    for step in traj.steps:
        for m in step.mutations:
            if m.kind in (
                "reorder_operations",
                "add_priority_heuristic",
                "add_dispatch_rule",
                "add_dependency_constraint",
            ):
                return True
    return False


# ── extractor registry ───────────────────────────────────────────────


ALL_FEATURE_EXTRACTORS: tuple[FeatureExtractor, ...] = (
    extract_pid_features,
    extract_systems_features,
    extract_optimizer_features,
    # Sprint 3 收尾 — family-specific extractors
    extract_aes_features,
    extract_cuda_features,
    extract_scheduling_features,
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
    "extract_aes_features",
    "extract_cuda_features",
    "extract_scheduling_features",
    "from_baseline_archive_pair",
    "from_iteration_dir",
]
