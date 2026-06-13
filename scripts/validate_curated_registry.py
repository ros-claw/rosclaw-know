#!/usr/bin/env python3
"""scripts/validate_curated_registry.py — schema + policy checks for the YAML registry.

Usage::

    python scripts/validate_curated_registry.py [--registry-root PATH]

Exit codes:
    0  registry is valid
    1  schema or policy violation found
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.curated_registry import (  # noqa: E402
    DOMAIN_VALUES,
    EVIDENCE_STATUS_VALUES,
    SOURCE_TIER_VALUES,
    STATUS_VALUES,
    load_registry,
    registry_root,
)


class RegistryValidator:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, path: Path | str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def validate(self, entries: list[Any], root: Path) -> bool:
        if not entries:
            self.add(root, "no registry entries found")
            return False

        # 1. id uniqueness
        ids = [e.id for e in entries]
        duplicates = {iid for iid, count in Counter(ids).items() if count > 1}
        for iid in sorted(duplicates):
            self.add(root, f"duplicate pattern id: {iid}")

        for entry in entries:
            path_hint = f"{entry.id}.yaml"

            # 2. source_tier legal (Pydantic already enforces, but keep for CLI)
            if entry.source_tier not in SOURCE_TIER_VALUES:
                self.add(path_hint, f"invalid source_tier: {entry.source_tier}")

            # 3. status legal
            if entry.status not in STATUS_VALUES:
                self.add(path_hint, f"invalid status: {entry.status}")

            # 4. runtime_eligible type is bool via Pydantic

            # 5. domain legal
            if entry.domain not in DOMAIN_VALUES:
                self.add(path_hint, f"invalid domain: {entry.domain}")

            # 6. topic_group + topic_tag non-empty
            if not entry.topic_group:
                self.add(path_hint, "topic_group must be non-empty")
            if not entry.topic_tag:
                self.add(path_hint, "topic_tag must be non-empty")

            # 7. matched_keywords.include non-empty (Pydantic)

            # 8. body fields non-empty
            for field in ("symptom", "diagnosis", "fix", "anti_pattern", "expected_signal"):
                value = getattr(entry.body, field, "")
                if not isinstance(value, str) or not value.strip():
                    self.add(path_hint, f"body.{field} must be a non-empty string")

            # 9. A/S tier routing_guard coverage
            if entry.source_tier in ("S_CURATED_VERIFIED", "A_CURATED_REVIEWED"):
                if len(entry.routing_guard.positive_queries) < 1:
                    self.add(
                        path_hint,
                        f"{entry.source_tier} tier requires at least 1 positive_query",
                    )
                if len(entry.routing_guard.collateral_queries) < 2:
                    self.add(
                        path_hint,
                        f"{entry.source_tier} tier requires at least 2 collateral_queries",
                    )

            # 10. evidence statuses legal
            for field in ("retrieval_status", "llm_judge_status", "official_verifier_status"):
                value = getattr(entry.evidence, field, None)
                if value not in EVIDENCE_STATUS_VALUES:
                    self.add(path_hint, f"invalid evidence.{field}: {value}")

            # Demoted status consistency
            if entry.status == "demoted" and entry.source_tier != "F_DEMOTED":
                self.add(
                    path_hint,
                    "status=demoted requires source_tier=F_DEMOTED",
                )
            if entry.source_tier == "F_DEMOTED" and entry.demotion.demote_reason is None:
                self.add(
                    path_hint,
                    "F_DEMOTED tier requires demotion.demote_reason",
                )

            # Runtime eligibility consistency
            if entry.status == "demoted" and entry.runtime_eligible:
                self.add(
                    path_hint,
                    "status=demoted pattern must have runtime_eligible=false",
                )

        return len(self.errors) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the curated YAML registry")
    ap.add_argument(
        "--registry-root",
        type=Path,
        default=None,
        help="Override the registry root directory",
    )
    args = ap.parse_args()

    root = args.registry_root or registry_root()
    print(f"[validate] registry root: {root}")

    try:
        entries = load_registry(root)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] failed to load registry: {exc}", file=sys.stderr)
        return 1

    validator = RegistryValidator()
    ok = validator.validate(entries, root)

    print(f"[validate] entries: {len(entries)}")
    if not ok:
        print(f"[error] {len(validator.errors)} validation failure(s):", file=sys.stderr)
        for err in validator.errors:
            print(f"  • {err}", file=sys.stderr)
        return 1

    print("[validate] OK — registry passes all checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
