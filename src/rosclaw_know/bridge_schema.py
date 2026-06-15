"""Bridge Schema v2 — strong contract for ``bridge_index.json``.

This module defines the canonical shape of a v2 bridge bundle, the set of
fields that affect retrieval/content_hash versus metadata_hash, and
validation helpers used by both the publisher and standalone scripts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Fields that change the *semantic content* used for embedding / retrieval.
# A change to any of these MUST rotate ``content_hash`` so rosclaw-how's
# ``/admin/reload`` knows the cluster needs re-embedding.
ROUTING_CRITICAL_FIELDS: tuple[str, ...] = (
    "standard_name",
    "domain",
    "robot_type",
    "topic_group",
    "topic_tag",
    "matched_keywords",
    "associated_patterns",
    "source",
    "source_tier",
    "status",
    "runtime_eligible",
    "priority",
)

# Fields that affect governance / lifecycle but do NOT change the embedding.
# A change to any of these rotates ``metadata_hash`` while keeping
# ``content_hash`` stable, avoiding unnecessary re-embeds when evidence is
# updated.
METADATA_FIELDS: tuple[str, ...] = (
    "routing_guard",
    "evidence",
    "demotion",
)

SOURCE_TIER_VALUES = {
    "S_CURATED_VERIFIED",
    "A_CURATED_REVIEWED",
    "B_TRAJECTORY_MINED",
    "C_MUSE_SYNTH",
    "D_AUTODRAFT",
    "F_DEMOTED",
}

STATUS_VALUES = {"active", "demoted", "draft"}
EVIDENCE_STATUS_VALUES = {"passed", "failed", "unstable", "not_started"}
DEMOTE_REASON_VALUES = {
    "negative_transfer",
    "low_coverage",
    "superseded",
    "schema_invalid",
}

# Accepted schema_version declarations for a v2 bridge bundle.
# All valid declarations are normalized to the canonical string "2.0".
_SCHEMA_VERSION_VALUES = (2, "2", "2.0", "v2")


class RoutingGuardV2(BaseModel):
    positive_queries: list[str] = Field(default_factory=list)
    collateral_queries: list[str] = Field(default_factory=list)
    adversarial_queries: list[str] = Field(default_factory=list)
    negative_signatures: list[str] = Field(default_factory=list)
    saturation_signatures: list[str] = Field(default_factory=list)


class EvidenceV2(BaseModel):
    retrieval_status: Literal["passed", "failed", "unstable", "not_started"]
    llm_judge_status: Literal["passed", "failed", "unstable", "not_started"]
    official_verifier_status: Literal["passed", "failed", "unstable", "not_started"]
    last_verified_panel: str | None = None
    notes: list[str] = Field(default_factory=list)


class DemotionV2(BaseModel):
    demote_reason: (
        Literal["negative_transfer", "low_coverage", "superseded", "schema_invalid"] | None
    ) = None
    confidence_score: float | None = None


class BridgeClusterV2(BaseModel):
    """One cluster entry in a v2 ``bridge_index.json``.

    Extra fields (e.g. ``cross_domain_analogies``, ``safety_label``) are
    allowed for backward compatibility but are not part of the v2 contract.
    """

    standard_name: str
    domain: str
    robot_type: str | None = None
    topic_group: str
    topic_tag: str
    source: str
    source_tier: Literal[
        "S_CURATED_VERIFIED",
        "A_CURATED_REVIEWED",
        "B_TRAJECTORY_MINED",
        "C_MUSE_SYNTH",
        "D_AUTODRAFT",
        "F_DEMOTED",
    ]
    status: Literal["active", "demoted", "draft"]
    runtime_eligible: bool
    priority: int = 0
    matched_keywords: list[str]
    associated_patterns: list[str]
    routing_guard: RoutingGuardV2
    evidence: EvidenceV2
    demotion: DemotionV2 | None = None
    content_hash: str
    metadata_hash: str | None = None
    safety_label: str | None = None

    @field_validator("standard_name")
    @classmethod
    def _standard_name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("standard_name must be non-empty")
        return v

    @field_validator("matched_keywords")
    @classmethod
    def _matched_keywords_nonempty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("matched_keywords must not be empty")
        return v

    model_config = {"extra": "allow"}


class BridgeIndexV2(BaseModel):
    schema_version: str = "2.0"
    symptom_clusters: dict[str, BridgeClusterV2]
    safety_label_index: dict[str, list[str]] | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _canonicalize_schema_version(cls, v: object) -> object:
        if v in _SCHEMA_VERSION_VALUES:
            return "2.0"
        return v

    @field_validator("schema_version", mode="after")
    @classmethod
    def _enforce_canonical_schema_version(cls, v: str) -> str:
        if v != "2.0":
            raise ValueError(f"unsupported schema_version: {v!r}")
        return v

    model_config = {"extra": "allow"}


def _normalize_for_hash(value: Any) -> Any:
    """Canonicalize a value before hashing so order-insensitive lists hash stably."""
    if isinstance(value, list):
        try:
            return sorted(_normalize_for_hash(v) for v in value)
        except TypeError:
            return sorted(
                (_normalize_for_hash(v) for v in value),
                key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False),
            )
    if isinstance(value, dict):
        return {k: _normalize_for_hash(value[k]) for k in sorted(value)}
    return value


def compute_content_hash(cluster: dict[str, Any]) -> str:
    """Deterministic sha256 over routing-critical fields.

    The hash deliberately excludes ``content_hash`` and ``metadata_hash``
    themselves and any ephemeral observability fields.
    """
    payload = {
        f: _normalize_for_hash(cluster.get(f)) for f in ROUTING_CRITICAL_FIELDS if f in cluster
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def compute_metadata_hash(cluster: dict[str, Any]) -> str:
    """Deterministic sha256 over governance/lifecycle fields."""
    payload = {f: _normalize_for_hash(cluster.get(f)) for f in METADATA_FIELDS if f in cluster}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def validate_bridge_index(
    data: dict[str, Any], code_patterns_dir: Path | None = None
) -> dict[str, Any]:
    """Validate a raw bridge_index dict and return a structured report.

    Supports a mixed v1/v2 bundle: clusters that declare ``source``,
    ``status``, and ``runtime_eligible`` are validated as Bridge Schema v2
    (including content_hash recomputation). Clusters missing those fields
    are treated as legacy v1 and only checked for tier/topic compatibility.

    Returns ``{"ok": bool, "errors": list[str], "warnings": list[str]}``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    schema_version = data.get("schema_version")
    if schema_version is None:
        errors.append("missing schema_version")
    elif schema_version not in _SCHEMA_VERSION_VALUES:
        errors.append(f"unsupported schema_version: {schema_version!r}")

    clusters = data.get("symptom_clusters")
    if clusters is None:
        errors.append("missing symptom_clusters")
        return {"ok": False, "errors": errors, "warnings": warnings}
    if not isinstance(clusters, dict):
        errors.append("symptom_clusters must be a dict")
        return {"ok": False, "errors": errors, "warnings": warnings}

    if not clusters:
        warnings.append("symptom_clusters is empty")

    code_pattern_ids: set[str] = set()
    if code_patterns_dir is not None and code_patterns_dir.exists():
        code_pattern_ids = {p.stem for p in code_patterns_dir.iterdir() if p.suffix == ".md"}

    v2_cluster_count = 0
    legacy_cluster_count = 0

    for cluster_id, raw in clusters.items():
        if not isinstance(raw, dict):
            errors.append(f"{cluster_id}: cluster is not a dict")
            continue

        is_v2 = all(k in raw for k in ("source", "status", "runtime_eligible"))

        if is_v2:
            v2_cluster_count += 1
            try:
                cluster = BridgeClusterV2(**raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{cluster_id}: {exc}")
                continue

            prefix = f"{cluster_id}"
            if cluster.source == "curated" and (not cluster.topic_group or not cluster.topic_tag):
                errors.append(f"{prefix}: curated cluster missing topic_group or topic_tag")

            if cluster.source_tier == "F_DEMOTED" and (
                cluster.demotion is None or not cluster.demotion.demote_reason
            ):
                errors.append(f"{prefix}: F_DEMOTED cluster must have demotion.demote_reason")

            if cluster.source_tier in ("S_CURATED_VERIFIED", "A_CURATED_REVIEWED"):
                if not cluster.routing_guard.positive_queries:
                    warnings.append(f"{prefix}: A/S tier cluster has no positive_queries")
                if len(cluster.routing_guard.collateral_queries) < 2:
                    warnings.append(
                        f"{prefix}: A/S tier cluster has fewer than 2 collateral_queries"
                    )

            expected_content_hash = compute_content_hash(raw)
            if cluster.content_hash != expected_content_hash:
                errors.append(
                    f"{prefix}: content_hash mismatch (expected {expected_content_hash}, got {cluster.content_hash})"
                )

            expected_metadata_hash = compute_metadata_hash(raw)
            if (
                cluster.metadata_hash is not None
                and cluster.metadata_hash != expected_metadata_hash
            ):
                warnings.append(
                    f"{prefix}: metadata_hash mismatch (expected {expected_metadata_hash}, got {cluster.metadata_hash})"
                )
        else:
            legacy_cluster_count += 1
            source_tier = raw.get("source_tier")
            if source_tier and source_tier not in SOURCE_TIER_VALUES:
                errors.append(f"{cluster_id}: invalid source_tier {source_tier!r}")
            source = raw.get("source")
            if source == "curated" and (not raw.get("topic_group") or not raw.get("topic_tag")):
                errors.append(f"{cluster_id}: curated cluster missing topic_group or topic_tag")
            # Skip content_hash check for legacy clusters — their hash was
            # computed with the v1 algorithm.

        if code_patterns_dir is not None and code_pattern_ids:
            associated = raw.get("associated_patterns") or []
            for pattern_id in associated:
                if pattern_id not in code_pattern_ids:
                    warnings.append(
                        f"{cluster_id}: associated pattern {pattern_id!r} missing from code_patterns"
                    )

    if v2_cluster_count and legacy_cluster_count:
        warnings.append(
            f"mixed v1/v2 bundle: {v2_cluster_count} v2 clusters, {legacy_cluster_count} legacy clusters"
        )

    return {"ok": not errors, "errors": errors, "warnings": warnings}


__all__ = [
    "BridgeClusterV2",
    "BridgeIndexV2",
    "DemotionV2",
    "EvidenceV2",
    "RoutingGuardV2",
    "ROUTING_CRITICAL_FIELDS",
    "METADATA_FIELDS",
    "SOURCE_TIER_VALUES",
    "STATUS_VALUES",
    "compute_content_hash",
    "compute_metadata_hash",
    "validate_bridge_index",
]
