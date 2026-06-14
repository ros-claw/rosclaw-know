"""Run a single (task, arm, seed) trial through a backend and score it."""

from __future__ import annotations

import logging

from rosclaw_know.ab_harness import ArmName, TaskRunResult

from .sandbox import AgentCodeError, AgentTimeoutError
from .synthetic_tasks import get_scoring_fn
from .types import AgentBackend, EvalTask

log = logging.getLogger("rosclaw_know.agent_eval.task_runner")


def run_one_with_code(
    task: EvalTask, backend: AgentBackend, arm: ArmName, seed: int
) -> tuple[TaskRunResult, str]:
    """Generate code from ``backend`` and score it with the task's simulator.

    Returns both the :class:`TaskRunResult` and the generated code string so
    callers can persist it for later qualitative review.
    """
    try:
        code = backend.run(task, arm, seed)
    except Exception as exc:  # noqa: BLE001
        log.warning("backend failed for %s/%s seed %s: %s", task.task_id, arm, seed, exc)
        return (
            TaskRunResult(
                task_id=task.task_id,
                arm=arm,
                seed=seed,
                score=None,
                objective_direction=task.objective_direction,
                valid=False,
            ),
            "",
        )

    try:
        score, hint_use_rate = get_scoring_fn(task.scoring_fn_name)(seed, code, task.params)
    except (AgentCodeError, AgentTimeoutError, Exception) as exc:  # noqa: BLE001
        log.warning("scoring failed for %s/%s seed %s: %s", task.task_id, arm, seed, exc)
        return (
            TaskRunResult(
                task_id=task.task_id,
                arm=arm,
                seed=seed,
                score=None,
                objective_direction=task.objective_direction,
                valid=False,
            ),
            code,
        )

    return (
        TaskRunResult(
            task_id=task.task_id,
            arm=arm,
            seed=seed,
            score=score,
            objective_direction=task.objective_direction,
            valid=True,
            hint_use_rate=hint_use_rate,
        ),
        code,
    )


def run_one(task: EvalTask, backend: AgentBackend, arm: ArmName, seed: int) -> TaskRunResult:
    """Convenience wrapper that returns only the :class:`TaskRunResult`."""
    return run_one_with_code(task, backend, arm, seed)[0]
