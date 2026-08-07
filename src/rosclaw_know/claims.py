"""Compile, validate and supersede evidence-backed knowledge claims."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from pydantic import Field

from rosclaw_know.contracts import (
    CompatibilityScopeV1,
    EvidenceRefV2,
    KnowledgeClaimV1,
    KnowledgeUnitV2,
    SourceDisagreementV1,
    SourceRecordV2,
    TruthQualityV1,
)
from rosclaw_know.contracts.base import StrictContract
from rosclaw_know.source_authority import source_authority
from rosclaw_know.store import KnowStore, RelationRecord
from rosclaw_know.wiki.models import WikiCompilationResult


def _id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


class ClaimAuditFailure(StrictContract):
    claim_id: str
    evidence_id: str | None = None
    reason: str


class ClaimAuditResult(StrictContract):
    checked: int
    passed: int
    failures: list[ClaimAuditFailure] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and self.checked == self.passed


def _truth(source: SourceRecordV2, *, inference: bool) -> TruthQualityV1:
    authority, base, reasons = source_authority(source)
    score = min(base, 0.8) if inference else base
    return TruthQualityV1(
        score=score,
        source_authority=authority,
        direct_evidence=not inference,
        inference=inference,
        reasons=reasons + (["compiler-derived synthesis"] if inference else []),
    )


def _scope(unit: KnowledgeUnitV2) -> CompatibilityScopeV1:
    constraints = unit.software_constraints
    return CompatibilityScopeV1(
        robot=unit.robot_constraints[0] if len(unit.robot_constraints) == 1 else None,
        hardware_arch=constraints.get("hardware_arch"),
        operating_system=constraints.get("os"),
        ros_distro=constraints.get("ros"),
        python=constraints.get("python"),
        cuda=constraints.get("cuda"),
        simulator=constraints.get("simulator"),
        library_versions={
            key: value
            for key, value in constraints.items()
            if key not in {"hardware_arch", "os", "ros", "python", "cuda", "simulator"}
        },
    )


def _focused_evidence(
    compilation: WikiCompilationResult,
    store: KnowStore,
    *,
    terms: list[str] | None = None,
    path_fragments: list[str] | None = None,
) -> EvidenceRefV2:
    """Return an exact matching line when possible, otherwise a pinned file excerpt."""

    terms = [term.casefold() for term in (terms or []) if term]
    fragments = [item.casefold() for item in (path_fragments or []) if item]
    candidates = sorted(
        compilation.evidence_refs,
        key=lambda evidence: (
            not any(fragment in evidence.path.casefold() for fragment in fragments),
            evidence.path,
        ),
    )
    for evidence in candidates:
        document = store.get_document(evidence.document_id)
        if document is None:
            continue
        for number, line in enumerate(document.content.splitlines(), start=1):
            if terms and not any(term in line.casefold() for term in terms):
                continue
            focused = evidence.model_copy(
                update={
                    "evidence_id": _id(
                        "evidence", f"{evidence.document_id}:{number}:{line.strip()}"
                    ),
                    "start_line": number,
                    "end_line": number,
                    "excerpt": line.strip()[:2000] or "Matched empty source line.",
                }
            )
            store.put_evidence(focused)
            return focused
    return candidates[0]


def put_claim_with_governance(
    store: KnowStore, claim: KnowledgeClaimV1
) -> KnowledgeClaimV1:
    """Persist one claim, superseding same-source history or queuing cross-source conflict."""

    disagreements: list[SourceDisagreementV1] = []
    for previous in store.list_claims(status="active"):
        if (
            previous.claim_id == claim.claim_id
            or previous.subject != claim.subject
            or previous.predicate != claim.predicate
            or previous.object == claim.object
        ):
            continue
        previous_sources = {evidence.source_id for evidence in previous.evidence_refs}
        claim_sources = {evidence.source_id for evidence in claim.evidence_refs}
        evidence = claim.evidence_refs[0]
        if previous_sources & claim_sources:
            updated = previous.model_copy(
                update={
                    "status": "superseded",
                    "valid_to": claim.observed_at,
                    "superseded_by": list(
                        dict.fromkeys([*previous.superseded_by, claim.claim_id])
                    ),
                    "updated_at": claim.created_at,
                }
            )
            store.put_claim(updated)
            relation_type = "SUPERSEDES"
        else:
            previous = previous.model_copy(
                update={
                    "contradicts": list(
                        dict.fromkeys([*previous.contradicts, claim.claim_id])
                    ),
                    "truth_quality": previous.truth_quality.model_copy(
                        update={
                            "contradiction_resolved": False,
                            "reasons": list(
                                dict.fromkeys(
                                    [
                                        *previous.truth_quality.reasons,
                                        "conflicting active claim from another source",
                                    ]
                                )
                            ),
                        }
                    ),
                    "updated_at": claim.created_at,
                }
            )
            store.put_claim(previous)
            claim = claim.model_copy(
                update={
                    "contradicts": list(
                        dict.fromkeys([*claim.contradicts, previous.claim_id])
                    ),
                    "truth_quality": claim.truth_quality.model_copy(
                        update={
                            "contradiction_resolved": False,
                            "reasons": list(
                                dict.fromkeys(
                                    [
                                        *claim.truth_quality.reasons,
                                        "conflicting active claim from another source",
                                    ]
                                )
                            ),
                        }
                    ),
                }
            )
            relation_type = "CONTRADICTS"
            snapshots = list(
                dict.fromkeys(
                    [*previous.source_snapshot_ids, *claim.source_snapshot_ids]
                )
            )
            disagreements.append(
                SourceDisagreementV1(
                    disagreement_id=_id(
                        "disagreement",
                        f"{previous.claim_id}:{claim.claim_id}:{previous.object}:{claim.object}",
                    ),
                    subject=claim.subject,
                    claim_ids=[previous.claim_id, claim.claim_id],
                    source_snapshot_ids=snapshots,
                    rationale=(
                        f"Active sources disagree on {claim.subject} / {claim.predicate}: "
                        f"{previous.object!r} versus {claim.object!r}."
                    ),
                    created_at=claim.created_at,
                    updated_at=claim.created_at,
                )
            )
        store.put_relation(
            RelationRecord(
                relation_id=_id(
                    "relation",
                    f"{claim.claim_id}:{relation_type}:{previous.claim_id}",
                ),
                from_id=claim.claim_id,
                from_type="knowledge_claim",
                relation_type=relation_type,  # type: ignore[arg-type]
                to_id=previous.claim_id,
                to_type="knowledge_claim",
                confidence=1.0,
                evidence_id=evidence.evidence_id,
                created_at=claim.created_at,
            )
        )
    store.put_claim(claim)
    for disagreement in disagreements:
        store.put_source_disagreement(disagreement)
    return claim


def compile_claims(
    compilation: WikiCompilationResult,
    units: list[KnowledgeUnitV2],
    *,
    source: SourceRecordV2,
    store: KnowStore,
) -> list[KnowledgeClaimV1]:
    """Create deterministic and synthesis claims, then supersede older facts."""

    now = datetime.now(UTC)
    claims: list[KnowledgeClaimV1] = []
    pages_by_type = {page.page_type: page for page in compilation.pages}
    units_by_type = {
        str(unit.applicability[-1]): unit for unit in units if unit.applicability
    }
    for component in compilation.components:
        component_evidence = next(
            (
                item
                for item in compilation.evidence_refs
                if item.path == component.path or item.path.startswith(component.path + "/")
            ),
            compilation.evidence_refs[0],
        )
        unit = units_by_type.get("architecture") or units[0]
        objects = [("contains_component", component.path)]
        if component.public_symbols:
            objects.append(("defines_symbols", ", ".join(component.public_symbols)))
        for predicate, object_text in objects:
            evidence = (
                _focused_evidence(
                    compilation,
                    store,
                    terms=[component.public_symbols[0]],
                    path_fragments=[component.path],
                )
                if predicate == "defines_symbols"
                else component_evidence
            )
            claim = KnowledgeClaimV1(
                claim_id=_id(
                    "claim",
                    f"{compilation.project_card.source_snapshot_id}:{component.component_id}:"
                    f"{predicate}:{object_text}",
                ),
                knowledge_unit_id=unit.knowledge_unit_id,
                subject=f"{compilation.project_card.project_id}:{component.path}",
                predicate=predicate,
                object=object_text,
                claim_type="deterministic_fact",
                source_snapshot_ids=[compilation.project_card.source_snapshot_id],
                evidence_refs=[evidence],
                truth_quality=_truth(source, inference=False),
                utility_score=0.5,
                compatibility_score=0.5,
                compatibility_status="unknown",
                compatibility_scope=_scope(unit),
                observed_at=now,
                generated_by="rosclaw_know.wiki.compiler:deterministic_facts",
                attributed_to=source.publisher or source.repository or source.title,
                created_at=now,
                updated_at=now,
            )
            claims.append(claim)
    inventory = compilation.inventory
    card = compilation.project_card
    architecture_evidence = next(
        (
            evidence
            for evidence in compilation.evidence_refs
            if evidence.path == ".rosclaw/repo_facts.json"
        ),
        compilation.evidence_refs[0],
    )
    build_evidence = _focused_evidence(
        compilation,
        store,
        terms=[*inventory.build_systems, *inventory.package_managers],
        path_fragments=[
            "pyproject.toml",
            "package.json",
            "package.xml",
            "cmakelists.txt",
            "cargo.toml",
            "setup.py",
        ],
    )
    issue_evidence = _focused_evidence(
        compilation,
        store,
        terms=[
            str((inventory.issues or inventory.releases or [{}])[0].get("title") or "")
        ],
        path_fragments=["issues.json", "releases.json", "pull_requests.json"],
    )
    compatibility_values = [
        *inventory.robots,
        *inventory.simulators,
        *inventory.ros_distros,
        *inventory.frameworks,
    ]
    compatibility_evidence = _focused_evidence(
        compilation,
        store,
        terms=compatibility_values,
        path_fragments=["readme", "docs/"],
    )
    issue_row = (inventory.issues or inventory.releases or inventory.pull_requests or [{}])[0]
    limitation = (
        card.known_limitations[0]
        if card.known_limitations
        else "Runtime behavior and hardware safety are not established by static indexing."
    )
    inventory_claims = [
        (
            "indexed_component_count",
            str(len(compilation.components)),
            "deterministic_fact",
            False,
            architecture_evidence,
        ),
        (
            "indexed_component_paths",
            ", ".join(component.path for component in compilation.components)
            or "no source-code component was present in the bounded snapshot",
            "deterministic_fact",
            False,
            architecture_evidence,
        ),
        (
            "build_and_package_systems",
            ", ".join([*inventory.build_systems, *inventory.package_managers]) or "unknown",
            "deterministic_fact",
            False,
            build_evidence,
        ),
        (
            "dependency_or_language_versions",
            ", ".join(f"{key}={value}" for key, value in inventory.versions.items())
            or ", ".join(inventory.languages)
            or "unknown",
            "source_fact",
            False,
            build_evidence,
        ),
        (
            "indexed_scope_limitation",
            limitation,
            "derived_claim",
            True,
            architecture_evidence,
        ),
        (
            "issue_release_or_pr",
            " | ".join(
                str(issue_row.get(key) or "") for key in ("id", "title", "state")
            ).strip(" |")
            or "no issue, release, or pull-request row was present in the bounded snapshot",
            "known_issue" if issue_row else "derived_claim",
            not bool(issue_row),
            issue_evidence,
        ),
        (
            "compatibility_markers",
            ", ".join(compatibility_values) or "unknown",
            "compatibility_constraint",
            not bool(compatibility_values),
            compatibility_evidence,
        ),
    ]
    unit = units_by_type.get("overview") or units[0]
    for predicate, object_text, claim_type, inference, evidence in inventory_claims:
        claims.append(
            KnowledgeClaimV1(
                claim_id=_id(
                    "claim",
                    f"{card.source_snapshot_id}:{card.project_id}:{predicate}:{object_text}",
                ),
                knowledge_unit_id=unit.knowledge_unit_id,
                subject=card.project_id,
                predicate=predicate,
                object=object_text,
                claim_type=claim_type,  # type: ignore[arg-type]
                source_snapshot_ids=[card.source_snapshot_id],
                evidence_refs=[evidence],
                truth_quality=_truth(source, inference=inference),
                utility_score=0.5,
                compatibility_score=0.5,
                compatibility_status="unknown",
                compatibility_scope=_scope(unit),
                observed_at=now,
                derived_from=[evidence.evidence_id] if inference else [],
                generated_by="rosclaw_know.wiki.compiler:deterministic_inventory",
                attributed_to=source.publisher or source.repository or source.title,
                created_at=now,
                updated_at=now,
            )
        )
    for release in inventory.releases[:10]:
        body = release.get("body", "").strip()
        if not body:
            continue
        release_name = release.get("tag") or release.get("title") or release.get("id")
        for predicate, claim_type, marker in (
            ("migration_note", "migration_note", r"\b(?:migrat|upgrade|breaking)\w*"),
            ("deprecated_api", "deprecated_api", r"\bdeprecat\w*"),
            (
                "release_compatibility_constraint",
                "compatibility_constraint",
                r"\b(?:compatib|requires?|supports?)\w*",
            ),
        ):
            match = re.search(marker, body, flags=re.IGNORECASE)
            if match is None:
                continue
            line = next(
                (
                    item.strip()
                    for item in body.splitlines()
                    if re.search(marker, item, flags=re.IGNORECASE)
                ),
                body[:1000],
            )[:2000]
            evidence = _focused_evidence(
                compilation,
                store,
                terms=[match.group(0)],
                path_fragments=["releases.json"],
            )
            claims.append(
                KnowledgeClaimV1(
                    claim_id=_id(
                        "claim",
                        f"{card.source_snapshot_id}:{card.project_id}:{predicate}:{line}",
                    ),
                    knowledge_unit_id=unit.knowledge_unit_id,
                    subject=card.project_id,
                    predicate=predicate,
                    object=f"{release_name}: {line}",
                    claim_type=claim_type,  # type: ignore[arg-type]
                    source_snapshot_ids=[card.source_snapshot_id],
                    evidence_refs=[evidence],
                    truth_quality=_truth(source, inference=False),
                    utility_score=0.5,
                    compatibility_score=0.5,
                    compatibility_status="unknown",
                    compatibility_scope=_scope(unit),
                    observed_at=now,
                    generated_by="rosclaw_know.wiki.compiler:release_notes",
                    attributed_to=source.publisher or source.repository or source.title,
                    created_at=now,
                    updated_at=now,
                )
            )
    for page_type, unit in units_by_type.items():
        page = pages_by_type.get(page_type)
        if page is None:
            continue
        claim = KnowledgeClaimV1(
            claim_id=_id(
                "claim",
                f"{page.snapshot_id}:{unit.knowledge_unit_id}:documents:{page.content_hash}",
            ),
            knowledge_unit_id=unit.knowledge_unit_id,
            subject=compilation.project_card.project_id,
            predicate=f"documents_{page_type}",
            object=page.summary,
            claim_type="derived_claim",
            source_snapshot_ids=unit.source_snapshot_ids,
            evidence_refs=unit.evidence_refs,
            truth_quality=_truth(source, inference=True),
            utility_score=0.5,
            compatibility_score=0.5,
            compatibility_status="unknown",
            compatibility_scope=_scope(unit),
            observed_at=now,
            derived_from=[evidence.evidence_id for evidence in unit.evidence_refs],
            generated_by="rosclaw_know.wiki.compiler:evidence_linked_synthesis",
            attributed_to=source.publisher or source.repository or source.title,
            created_at=now,
            updated_at=now,
        )
        claims.append(claim)
    with store.transaction():
        claims = [put_claim_with_governance(store, claim) for claim in claims]
    return claims


def audit_claims(store: KnowStore, claims: list[KnowledgeClaimV1] | None = None) -> ClaimAuditResult:
    selected = claims if claims is not None else store.list_claims()
    failures: list[ClaimAuditFailure] = []
    passed = 0
    for claim in selected:
        before = len(failures)
        for evidence in claim.evidence_refs:
            snapshot = store.get_snapshot(evidence.snapshot_id)
            document = store.get_document(evidence.document_id)
            stored_evidence = store.get_evidence(evidence.evidence_id)
            if snapshot is None:
                failures.append(
                    ClaimAuditFailure(
                        claim_id=claim.claim_id,
                        evidence_id=evidence.evidence_id,
                        reason="snapshot_missing",
                    )
                )
            if document is None:
                failures.append(
                    ClaimAuditFailure(
                        claim_id=claim.claim_id,
                        evidence_id=evidence.evidence_id,
                        reason="document_missing",
                    )
                )
            elif document.content_hash != evidence.content_hash:
                failures.append(
                    ClaimAuditFailure(
                        claim_id=claim.claim_id,
                        evidence_id=evidence.evidence_id,
                        reason="document_hash_mismatch",
                    )
                )
            if stored_evidence != evidence:
                failures.append(
                    ClaimAuditFailure(
                        claim_id=claim.claim_id,
                        evidence_id=evidence.evidence_id,
                        reason="evidence_record_mismatch",
                    )
                )
        if len(failures) == before:
            passed += 1
    return ClaimAuditResult(checked=len(selected), passed=passed, failures=failures)


__all__ = [
    "ClaimAuditResult",
    "audit_claims",
    "compile_claims",
    "put_claim_with_governance",
]
