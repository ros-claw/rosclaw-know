"""Physical constraints schema for TaskCard v1."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ConstraintCheck(_Base):
    method: str
    expression: str


class HardConstraint(_Base):
    id: str
    type: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    check: ConstraintCheck
    violation_action: Literal["STOP", "STOP_AND_RECOVER", "BLOCK", "ABORT"]
    how_strategy: Literal["SAFETY", "ABSTAIN", "ABSTAIN_OR_SAFETY", "CATALYST"] | None = None


class SoftConstraint(_Base):
    id: str
    type: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    recommended_value: dict[str, Any] | None = None
    check: ConstraintCheck | None = None
    violation_action: Literal["REQUIRE_CONFIRMATION", "REQUEST_REPLAN", "WARN"] = "WARN"


class OperationalConstraint(_Base):
    id: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    violation_action: Literal[
        "REJECT_EXPERIMENT", "REJECT_PLAN", "REQUIRE_APPROVAL", "WARN"
    ] = "REJECT_EXPERIMENT"


class ContextConstraint(_Base):
    id: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    valid_context: dict[str, Any] = Field(default_factory=dict)
    violation_action: Literal["REQUIRE_RECOMPILE", "REQUIRE_CONFIRMATION", "WARN"] = (
        "REQUIRE_RECOMPILE"
    )


class PhysicalConstraints(_Base):
    schema_version: Literal["physical_constraints.v1"] = "physical_constraints.v1"
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    soft_constraints: list[SoftConstraint] = Field(default_factory=list)
    operational_constraints: list[OperationalConstraint] = Field(default_factory=list)
    context_constraints: list[ContextConstraint] = Field(default_factory=list)


__all__ = [
    "PhysicalConstraints",
    "HardConstraint",
    "SoftConstraint",
    "OperationalConstraint",
    "ContextConstraint",
    "ConstraintCheck",
]
