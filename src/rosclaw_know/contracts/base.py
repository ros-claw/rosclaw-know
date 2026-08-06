"""Versioned wire-contract primitives shared by Know v2 models."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

_VERSION_RE = re.compile(r"^(?P<namespace>[a-z][a-z0-9_.-]*)\.v(?P<major>[1-9][0-9]*)$")
ContractT = TypeVar("ContractT", bound="StrictContract")


class ContractVersionError(ValueError):
    """Raised when peers do not share a compatible contract version."""


class StrictContract(BaseModel):
    """Base for authoritative server-side contracts.

    Unknown fields are rejected deliberately. Compatibility readers should
    explicitly project a newer payload to a supported schema before calling
    ``model_validate``; silently accepting arbitrary fields hides drift.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=False,
        validate_assignment=True,
        str_strip_whitespace=True,
    )
    SCHEMA_VERSION: ClassVar[str | None] = None

    @classmethod
    def validate_wire_json(cls: type[ContractT], payload: str | bytes) -> ContractT:
        return cls.model_validate_json(payload)

    def to_wire_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


class TimestampedContract(StrictContract):
    """Strict contract with timezone-aware creation timestamp."""

    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created_at_aware(cls, value: datetime) -> datetime:
        return ensure_aware(value)  # type: ignore[return-value]


def parse_schema_version(value: str) -> tuple[str, int]:
    match = _VERSION_RE.fullmatch(value)
    if not match:
        raise ContractVersionError(f"invalid schema version: {value!r}")
    return match.group("namespace"), int(match.group("major"))


def negotiate_schema_version(*, offered: Iterable[str], supported: Iterable[str]) -> str:
    """Choose the highest exact wire version shared by both peers.

    Major versions are never coerced. The explicit exact-match rule keeps
    unknown-field and required-field behavior deterministic across services.
    """

    offered_set = set(offered)
    supported_set = set(supported)
    common = offered_set & supported_set
    if not common:
        raise ContractVersionError(
            "no compatible schema version; "
            f"offered={sorted(offered_set)!r}, supported={sorted(supported_set)!r}"
        )
    return max(common, key=lambda item: parse_schema_version(item)[1])


def export_contract_schemas(models: Iterable[type[StrictContract]]) -> dict[str, Any]:
    """Return deterministic JSON Schemas keyed by schema version/model name."""

    result: dict[str, Any] = {}
    for model in models:
        key = model.SCHEMA_VERSION or model.__name__
        result[key] = model.model_json_schema(mode="serialization")
    return dict(sorted(result.items()))
