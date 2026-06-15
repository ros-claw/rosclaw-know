"""YAML-based curated pattern registry.

Replaces the legacy ``CURATED_SAFETY_PATTERNS`` constant list with a directory
of reviewable, diffable YAML files under ``data/curated_registry/``.

Usage::

    from rosclaw_know.curated_registry import load_curated_patterns

    patterns = load_curated_patterns()   # respects ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED

The legacy dataclass ``CuratedPattern`` is still the runtime-facing API;
``load_curated_patterns`` returns ``list[CuratedPattern]`` so callers do not
need to change.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .config import CURATED_REGISTRY_DIR, PROJECT_ROOT
from .curated_patterns import CuratedPattern

log = logging.getLogger("rosclaw_know.curated_registry")

DEFAULT_REGISTRY_ROOT = CURATED_REGISTRY_DIR
ENABLED_VAR = "ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED"

STATUS_VALUES = {"active", "demoted", "draft"}
SOURCE_TIER_VALUES = {
    "S_CURATED_VERIFIED",
    "A_CURATED_REVIEWED",
    "B_TRAJECTORY_MINED",
    "C_MUSE_SYNTH",
    "D_AUTODRAFT",
    "F_DEMOTED",
}
DOMAIN_VALUES = {
    "Control_Locomotion",
    "Learning_Training",
    "Memory_Reasoning",
    "Perception_Vision",
    "Planning_Decision",
    "Systems_Compute",
}
EVIDENCE_STATUS_VALUES = {"passed", "failed", "unstable", "not_started"}
DEMOTE_REASON_VALUES = {
    "negative_transfer",
    "low_coverage",
    "superseded",
    "schema_invalid",
    None,
}


class MatchedKeywords(BaseModel):
    include: list[str]
    exclude: list[str] = Field(default_factory=list)

    @field_validator("include")
    @classmethod
    def _include_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("matched_keywords.include must not be empty")
        return v


class RoutingGuard(BaseModel):
    positive_queries: list[str] = Field(default_factory=list)
    collateral_queries: list[str] = Field(default_factory=list)
    adversarial_queries: list[str] = Field(default_factory=list)
    negative_signatures: list[str] = Field(default_factory=list)
    saturation_signatures: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    retrieval_status: Literal["passed", "failed", "unstable", "not_started"]
    llm_judge_status: Literal["passed", "failed", "unstable", "not_started"]
    official_verifier_status: Literal["passed", "failed", "unstable", "not_started"]
    last_verified_panel: str | None = None
    notes: list[str] = Field(default_factory=list)


class Demotion(BaseModel):
    demote_reason: (
        Literal[
            "negative_transfer",
            "low_coverage",
            "superseded",
            "schema_invalid",
        ]
        | None
    ) = None
    confidence_score: float | None = None


class Body(BaseModel):
    symptom: str
    diagnosis: str
    fix: str
    anti_pattern: str
    expected_signal: str
    before_code: str | None = None
    after_code: str | None = None
    cross_domain_hints: list[dict[str, str]] = Field(default_factory=list)


class CuratedRegistryEntry(BaseModel):
    """One curated pattern as stored in the YAML registry."""

    id: str
    title: str
    status: Literal["active", "demoted", "draft"]
    runtime_eligible: bool
    source_tier: Literal[
        "S_CURATED_VERIFIED",
        "A_CURATED_REVIEWED",
        "B_TRAJECTORY_MINED",
        "C_MUSE_SYNTH",
        "D_AUTODRAFT",
        "F_DEMOTED",
    ]
    domain: Literal[
        "Control_Locomotion",
        "Learning_Training",
        "Memory_Reasoning",
        "Perception_Vision",
        "Planning_Decision",
        "Systems_Compute",
    ]
    robot_type: str | None = None
    topic_group: str
    topic_tag: str
    safety_label: str
    standard_name: str
    matched_keywords: MatchedKeywords
    log_signatures: list[str] = Field(default_factory=list)
    routing_guard: RoutingGuard
    evidence: Evidence
    demotion: Demotion
    body: Body

    @field_validator("id")
    @classmethod
    def _id_no_spaces(cls, v: str) -> str:
        if not v or " " in v:
            raise ValueError("id must be non-empty and contain no spaces")
        return v

    def to_curated_pattern(self) -> CuratedPattern:
        """Convert this registry entry to the legacy runtime dataclass."""
        return _to_curated_pattern(self)


def registry_root() -> Path:
    """Return the directory holding the curated YAML registry."""
    raw = os.environ.get("ROSCLAW_KNOW_CURATED_REGISTRY_ROOT", "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    return DEFAULT_REGISTRY_ROOT


def registry_enabled() -> bool:
    """True when the YAML registry should override legacy constants.

    Default is ``False`` to keep existing tests stable. Set
    ``ROSCLAW_KNOW_CURATED_REGISTRY_ENABLED=1`` to activate.
    """
    raw = os.environ.get(ENABLED_VAR, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return False


def iter_registry_files(root: Path | None = None) -> Iterator[Path]:
    root = root or registry_root()
    if not root.exists():
        return
    yield from sorted(root.rglob("*.yaml"))


def load_registry(root: Path | None = None) -> list[CuratedRegistryEntry]:
    """Load and validate every YAML file under the registry root."""
    entries: list[CuratedRegistryEntry] = []
    for path in iter_registry_files(root):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected mapping, got {type(data).__name__}")
        try:
            entries.append(CuratedRegistryEntry(**data))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{path}: validation failed: {exc}") from exc
    return entries


def _to_curated_pattern(entry: CuratedRegistryEntry) -> CuratedPattern:
    """Convert a registry entry to the legacy ``CuratedPattern`` dataclass."""
    return CuratedPattern(
        pattern_id=entry.id,
        safety_label=entry.safety_label,
        standard_name=entry.standard_name,
        domain=entry.domain,
        matched_keywords=list(entry.matched_keywords.include),
        fix_pattern=entry.body.fix,
        failed_attempt=entry.body.anti_pattern,
        before_code=entry.body.before_code or "",
        after_code=entry.body.after_code or "",
        cross_domain_hints=entry.body.cross_domain_hints,
        topic_group=entry.topic_group,
        topic_tag=entry.topic_tag,
        robot_type=entry.robot_type,
        status=entry.status,
        runtime_eligible=entry.runtime_eligible,
        source_tier=entry.source_tier,
        routing_guard=entry.routing_guard.model_dump(),
        evidence=entry.evidence.model_dump(),
        demotion=entry.demotion.model_dump() if entry.demotion else None,
    )


def load_curated_patterns() -> list[CuratedPattern]:
    """Runtime-compatible loader.

    Returns registry entries when enabled, otherwise the legacy constants.
    """
    if registry_enabled():
        log.info("Loading curated patterns from YAML registry: %s", registry_root())
        return [_to_curated_pattern(e) for e in load_registry()]
    from .curated_patterns import CURATED_SAFETY_PATTERNS

    return list(CURATED_SAFETY_PATTERNS)


__all__ = [
    "Body",
    "CuratedRegistryEntry",
    "Demotion",
    "Evidence",
    "MatchedKeywords",
    "RoutingGuard",
    "load_curated_patterns",
    "load_registry",
    "registry_enabled",
    "registry_root",
]
