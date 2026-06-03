"""Sprint 8: synthetic ``run_fn`` for the A/B harness (CI demo).

Produces realistic-shaped Frontier-Engineering results without
actually invoking the benchmark — pure-Python, deterministic per seed,
and direction-aware (maximize/minimize).

The point of this backend is to let CI exercise the analysis pipeline
end-to-end: rank computation, performance profile, acceptance gates.
For production, swap in a wrapper around
``frontier_eng/run_search.py`` (or equivalent) that translates
``(TaskSpec, ArmName, seed) → TaskRunResult``.

Per-arm effect sizes (centred on ``baseline = 0``)::

    baseline:                 0.000  noise 0.020
    true_know:                0.150  noise 0.025
    placebo_know:             0.020  noise 0.025
    shuffled_know:           −0.030  noise 0.030
    task_pack_only:           0.090  noise 0.020
    task_pack_plus_catalyst:  0.180  noise 0.025

These constants are deliberately spread out so a 10-task × 3-seed
matrix reliably satisfies the §Sprint 8 acceptance gates; they
also leave room for variance on small samples so the rank-based
analysis doesn't get a free pass.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random

from .ab_harness import ArmName, TaskRunResult, TaskSpec

log = logging.getLogger("rosclaw_know.ab_synthetic")


# ── Effect-size table ───────────────────────────────────────────────────


_ARM_EFFECT: dict[ArmName, tuple[float, float]] = {
    # arm → (mean uplift in *score units*, std)
    "baseline":                (0.000, 0.020),
    "true_know":               (0.150, 0.025),
    "placebo_know":            (0.020, 0.025),
    "shuffled_know":           (-0.030, 0.030),
    "task_pack_only":          (0.090, 0.020),
    "task_pack_plus_catalyst": (0.180, 0.025),
}


# Per-arm hint adoption rate (fraction of trials where the agent
# actually applied the hinted change).  Mirrors the design choice in
# Sprint 6 (placebo arm hint-use ~ 0 by construction).
_ARM_HINT_USE: dict[ArmName, float] = {
    "baseline":                0.0,
    "true_know":               0.75,
    "placebo_know":            0.05,
    "shuffled_know":           0.10,
    "task_pack_only":          0.50,
    "task_pack_plus_catalyst": 0.85,
}

# Per-arm invalid-trial rate.  Placebo + shuffled hints can occasionally
# break the agent (think "agent followed a bad suggestion and crashed
# the verifier"), while baseline is the gold-standard "doesn't crash".
_ARM_INVALID_RATE: dict[ArmName, float] = {
    "baseline":                0.02,
    "true_know":               0.03,
    "placebo_know":            0.05,
    "shuffled_know":           0.08,
    "task_pack_only":          0.03,
    "task_pack_plus_catalyst": 0.04,
}


def _stable_task_offset(task_id: str) -> float:
    """Per-task base score in [0.20, 0.80].

    Drawn from a sha256 of the task id so heterogeneous tasks don't
    cluster on the same score (mimicking real Frontier-Eng).  Stable
    across runs.
    """
    h = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return 0.20 + 0.60 * (int(h[:8], 16) / 0xFFFFFFFF)


def _seeded_rng(task_id: str, arm: str, seed: int) -> random.Random:
    """Per-trial deterministic RNG (task × arm × seed)."""
    key = f"{task_id}::{arm}::{seed}".encode("utf-8")
    h = hashlib.sha256(key).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


# ── main entry: the run_fn ───────────────────────────────────────────────


def synthetic_run_fn(
    task: TaskSpec, arm: ArmName, seed: int,
) -> TaskRunResult:
    """Plan §Sprint 8 synthetic backend.

    Returns deterministic ``TaskRunResult`` for ``(task, arm, seed)``.
    Direction-aware: for "minimize" tasks the effect is signed so
    "better than baseline" still means "this arm's score is lower".
    """
    rng = _seeded_rng(task.task_id, arm, seed)
    base = _stable_task_offset(task.task_id)
    mean_eff, std_eff = _ARM_EFFECT[arm]
    invalid_prob = _ARM_INVALID_RATE[arm]
    hint_use_prob = _ARM_HINT_USE[arm]

    if rng.random() < invalid_prob:
        return TaskRunResult(
            task_id=task.task_id,
            arm=arm,
            seed=seed,
            score=None,
            objective_direction=task.objective_direction,
            valid=False,
            hint_use_rate=0.0,
        )

    raw = rng.gauss(mean_eff, std_eff)
    if task.objective_direction == "minimize":
        # "Better than baseline" should still mean a *lower* score for
        # this task — flip the sign of the per-arm effect.
        raw = -raw
        # And the per-task baseline lives at the top half of the unit
        # interval so the metric is plausibly a "loss" or "makespan".
        base = 1.0 - base
        score = max(0.0, base + raw + rng.gauss(0, 0.005))
    else:
        score = min(1.0, base + raw + rng.gauss(0, 0.005))

    # Hint use is Bernoulli(hint_use_prob) per trial.
    used = rng.random() < hint_use_prob

    return TaskRunResult(
        task_id=task.task_id,
        arm=arm,
        seed=seed,
        score=round(score, 6),
        objective_direction=task.objective_direction,
        valid=True,
        hint_use_rate=1.0 if used else 0.0,
    )


__all__ = [
    "_ARM_EFFECT",
    "_ARM_HINT_USE",
    "_ARM_INVALID_RATE",
    "synthetic_run_fn",
]
