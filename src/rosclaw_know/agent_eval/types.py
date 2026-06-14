"""Shared types for the Phase 9 real-agent A/B harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from rosclaw_know.ab_harness import ArmName


@dataclass(frozen=True)
class EvalTask:
    """One eval task loaded from a YAML file."""

    task_id: str
    description: str
    entrypoint: str
    scoring_fn_name: str
    objective_direction: str
    metric_name: str
    max_iters: int
    params: dict[str, Any]
    canonical_hint: str = ""
    placebo_hint: str = ""
    shuffled_hint: str = ""
    task_pack_hint: str = ""


class AgentBackend(Protocol):
    """Pluggable agent that turns a task into an executable code string."""

    def run(self, task: EvalTask, arm: ArmName, seed: int) -> str: ...


__all__ = ["EvalTask", "AgentBackend"]
