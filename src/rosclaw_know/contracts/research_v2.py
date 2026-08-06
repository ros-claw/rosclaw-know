"""Research request contract."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, field_validator

from .base import StrictContract

SourceType = Literal[
    "repository",
    "paper",
    "official_documentation",
    "issue",
    "pull_request",
    "release",
    "web",
]


class ResearchConstraintsV2(StrictContract):
    robot_model: str | None = None
    simulator: str | None = None
    ros_distro: str | None = None
    language: list[str] = Field(default_factory=list)
    date_after: str | None = None
    software_versions: dict[str, str] = Field(default_factory=dict)


class ResearchRequestV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.research_request.v2"

    schema_version: Literal["rosclaw.know.research_request.v2"] = SCHEMA_VERSION
    request_id: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=2000)
    goal: str = Field(min_length=1, max_length=4000)
    perspectives: list[str] = Field(default_factory=list, max_length=20)
    source_types: list[SourceType] = Field(default_factory=list, max_length=20)
    constraints: ResearchConstraintsV2 = Field(default_factory=ResearchConstraintsV2)
    depth: Literal["shallow", "standard", "deep"] = "standard"
    max_sources: int = Field(default=20, ge=1, le=200)
    token_budget: int = Field(default=50_000, ge=1_000, le=2_000_000)

    @field_validator("perspectives", "source_types")
    @classmethod
    def _unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("values must be unique")
        return values
