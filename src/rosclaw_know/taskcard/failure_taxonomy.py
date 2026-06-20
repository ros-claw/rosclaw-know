"""Failure taxonomy schema for TaskCard v1."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class FailureCategory(_Base):
    id: str
    name: str
    severity_default: str
    description: str


class HowTrigger(_Base):
    enabled: bool = True
    query_hint: str = ""
    expected_strategy: Literal["SAFETY", "CATALYST", "ABSTAIN", "FREE_EXPLORATION"] | None = None


class MemoryWrite(_Base):
    enabled: bool = True
    event_type: str


class Failure(_Base):
    id: str
    category: str
    applies_to: list[str] = Field(default_factory=list)
    description: str
    observable_signals: list[str] = Field(default_factory=list)
    likely_causes: list[str] = Field(default_factory=list)
    severity: str
    recovery: list[str] = Field(default_factory=list)
    how_trigger: HowTrigger | None = None
    memory_write: MemoryWrite | None = None


class FailureTaxonomy(_Base):
    schema_version: Literal["failure_taxonomy.v1"] = "failure_taxonomy.v1"
    categories: list[FailureCategory] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)

    def failures_for_subtask(self, subtask_id: str) -> list[Failure]:
        return [f for f in self.failures if subtask_id in f.applies_to]


__all__ = [
    "FailureTaxonomy",
    "FailureCategory",
    "Failure",
    "HowTrigger",
    "MemoryWrite",
]
