"""Source, immutable snapshot and evidence contracts."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .base import StrictContract, ensure_aware

TrustTier = Literal["official", "primary", "curated", "community", "unknown"]
VersionKind = Literal["git_commit", "release", "document_version", "timestamp"]


class IntegrityV2(StrictContract):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str | None = None


class SourceRecordV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.source_record.v2"

    schema_version: Literal["rosclaw.know.source_record.v2"] = SCHEMA_VERSION
    source_id: str = Field(min_length=1, max_length=200)
    canonical_url: str = Field(min_length=1, max_length=4000)
    source_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=1000)
    publisher: str | None = None
    repository: str | None = None
    license: str | None = None
    trust_tier: TrustTier = "unknown"
    discovered_at: datetime
    latest_snapshot_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    provenance_status: Literal["verified", "legacy_unknown", "untrusted"] = "verified"

    @field_validator("discovered_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]


class SourceSnapshotV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.source_snapshot.v2"

    schema_version: Literal["rosclaw.know.source_snapshot.v2"] = SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1, max_length=240)
    source_id: str = Field(min_length=1, max_length=200)
    version_kind: VersionKind
    version_value: str = Field(min_length=1, max_length=500)
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,64}$")
    tag: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_snapshot_id: str | None = None
    supersedes_snapshot_id: str | None = None
    immutable: Literal[True] = True
    integrity: IntegrityV2

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return ensure_aware(value)

    @model_validator(mode="after")
    def _commit_has_sha(self) -> SourceSnapshotV2:
        if self.version_kind == "git_commit" and not self.commit_sha:
            raise ValueError("git_commit snapshots require commit_sha")
        if self.integrity.sha256 != self.content_hash:
            raise ValueError("integrity.sha256 must match content_hash")
        return self


class EvidenceRefV2(StrictContract):
    SCHEMA_VERSION: ClassVar[str] = "rosclaw.know.evidence_ref.v2"

    schema_version: Literal["rosclaw.know.evidence_ref.v2"] = SCHEMA_VERSION
    evidence_id: str = Field(min_length=1, max_length=240)
    source_id: str = Field(min_length=1, max_length=200)
    snapshot_id: str = Field(min_length=1, max_length=240)
    document_id: str = Field(min_length=1, max_length=240)
    path: str = Field(min_length=1, max_length=4000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    section: str | None = None
    url: str = Field(min_length=1, max_length=4000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _line_range(self) -> EvidenceRefV2:
        if self.end_line is not None and self.start_line is None:
            raise ValueError("end_line requires start_line")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be >= start_line")
        return self
