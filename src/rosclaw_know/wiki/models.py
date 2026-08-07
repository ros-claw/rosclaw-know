"""Project Wiki compilation result models."""

from __future__ import annotations

from pydantic import Field

from rosclaw_know.contracts import EvidenceRefV2, ProjectCardV2
from rosclaw_know.contracts.base import StrictContract
from rosclaw_know.store import ProjectComponentRecord, WikiPageRecord


class RepositoryInventory(StrictContract):
    paths: list[str]
    languages: list[str]
    build_systems: list[str]
    package_managers: list[str]
    frameworks: list[str]
    ros_distros: list[str]
    simulators: list[str]
    robots: list[str]
    has_ci: bool
    has_container: bool
    has_docs: bool
    has_examples: bool
    has_tests: bool
    unknowns: list[str] = Field(default_factory=list)
    file_symbols: dict[str, list[str]] = Field(default_factory=dict)
    file_imports: dict[str, list[str]] = Field(default_factory=dict)
    entrypoints: list[str] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    config_keys: dict[str, list[str]] = Field(default_factory=dict)
    releases: list[dict[str, str]] = Field(default_factory=list)
    issues: list[dict[str, str]] = Field(default_factory=list)
    pull_requests: list[dict[str, str]] = Field(default_factory=list)


class WikiCompilationResult(StrictContract):
    project_card: ProjectCardV2
    inventory: RepositoryInventory
    components: list[ProjectComponentRecord]
    pages: list[WikiPageRecord]
    evidence_refs: list[EvidenceRefV2]
    changed_paths: list[str]
    rebuilt_page_types: list[str]
    warnings: list[str] = Field(default_factory=list)
