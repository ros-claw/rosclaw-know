from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rosclaw_know.contracts import (
    PUBLIC_CONTRACTS,
    ContractVersionError,
    EvidenceRefV2,
    HowAdviceBundleV2,
    IntegrityV2,
    KnowledgeUnitV2,
    KnowledgeUsageFeedbackV1,
    KnowledgeVectorsV2,
    ReferenceContextV2,
    ReferencePackItemV2,
    ReferencePackV2,
    ResearchRequestV2,
    SourceSnapshotV2,
    export_contract_schemas,
    negotiate_schema_version,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
HASH = "a" * 64


def evidence() -> EvidenceRefV2:
    return EvidenceRefV2(
        evidence_id="ev-1",
        source_id="source-1",
        snapshot_id="snap-1",
        document_id="doc-1",
        path="src/controller.py",
        start_line=10,
        end_line=20,
        section="Controller",
        url="https://example.invalid/repo/blob/abc/src/controller.py#L10-L20",
        content_hash=HASH,
        excerpt="The controller clamps the integral term.",
    )


def unit() -> KnowledgeUnitV2:
    return KnowledgeUnitV2(
        knowledge_unit_id="unit-1",
        unit_type="implementation",
        title="Clamp the integral term",
        problem="Integral wind-up destabilizes recovery.",
        mechanism="Bound the accumulated error.",
        implementation="Clamp before producing the control recommendation.",
        applicability=["PID control"],
        limitations=["Requires tuned bounds"],
        contraindications=[],
        software_constraints={"ros": "humble"},
        hardware_constraints=[],
        robot_constraints=[],
        source_snapshot_ids=["snap-1"],
        evidence_refs=[evidence()],
        confidence=0.9,
        status="verified",
        created_at=NOW,
        updated_at=NOW,
        vectors=KnowledgeVectorsV2(problem=[0.1], content=[0.2]),
    )


def pack() -> ReferencePackV2:
    return ReferencePackV2(
        reference_pack_id="pack-1",
        query="PID wind-up",
        context=ReferenceContextV2(task="stabilize", ros_distro="humble"),
        generated_at=NOW,
        index_version="index-1",
        items=[
            ReferencePackItemV2(
                rank=1,
                project_id="project-1",
                knowledge_unit_ids=["unit-1"],
                title="Clamp the integral term",
                why_relevant="Matches the failure string and controller family.",
                mechanism="Bound accumulated error.",
                what_to_borrow=["anti-windup guard"],
                exact_files=["src/controller.py"],
                source_version="abc1234",
                evidence_refs=[evidence()],
                score=0.9,
                score_breakdown={"exact": 1.0, "rrf": 0.9},
            )
        ],
        token_budget=4000,
    )


@pytest.mark.parametrize(
    "contract",
    [
        ResearchRequestV2(request_id="research-1", topic="robot control", goal="find references"),
        unit(),
        pack(),
        HowAdviceBundleV2(
            advice_id="advice-1",
            mode="diagnose",
            context_hash=HASH,
            reference_pack_id="pack-1",
            summary="Inspect anti-windup configuration.",
            created_at=NOW,
        ),
        KnowledgeUsageFeedbackV1(
            feedback_id="feedback-1",
            reference_pack_id="pack-1",
            knowledge_unit_id="unit-1",
            verdict="useful",
            context_hash=HASH,
            origin="verifier",
            created_at=NOW,
        ),
    ],
)
def test_json_round_trip(contract):
    restored = type(contract).validate_wire_json(contract.to_wire_json())
    assert restored == contract


def test_unknown_fields_are_rejected():
    payload = {
        "request_id": "research-1",
        "topic": "robot control",
        "goal": "find references",
        "unexpected": True,
    }
    with pytest.raises(ValidationError, match="unexpected"):
        ResearchRequestV2.model_validate(payload)


def test_wrong_schema_version_is_rejected():
    payload = json.loads(pack().to_wire_json())
    payload["schema_version"] = "rosclaw.know.reference_pack.v3"
    with pytest.raises(ValidationError, match="schema_version"):
        ReferencePackV2.model_validate_json(json.dumps(payload))


def test_snapshot_is_immutable_and_integrity_matches():
    snapshot = SourceSnapshotV2(
        snapshot_id="snap-1",
        source_id="source-1",
        version_kind="git_commit",
        version_value="abc1234",
        commit_sha="abc1234",
        fetched_at=NOW,
        content_hash=HASH,
        integrity=IntegrityV2(sha256=HASH),
    )
    assert snapshot.immutable is True
    with pytest.raises(ValidationError):
        SourceSnapshotV2.model_validate({**snapshot.model_dump(), "immutable": False}, strict=True)


def test_evidence_must_be_pinned_to_declared_snapshot():
    payload = unit().model_dump()
    payload["source_snapshot_ids"] = ["another-snapshot"]
    with pytest.raises(ValidationError, match="declared source snapshot"):
        KnowledgeUnitV2.model_validate(payload)


def test_truncated_pack_requires_cursor():
    payload = pack().model_dump()
    payload["truncated"] = True
    with pytest.raises(ValidationError, match="continuation_cursor"):
        ReferencePackV2.model_validate(payload)


def test_version_negotiation_is_exact_and_highest():
    assert (
        negotiate_schema_version(
            offered=["rosclaw.test.v1", "rosclaw.test.v2"],
            supported=["rosclaw.test.v2", "rosclaw.test.v3"],
        )
        == "rosclaw.test.v2"
    )
    with pytest.raises(ContractVersionError, match="no compatible"):
        negotiate_schema_version(offered=["rosclaw.test.v1"], supported=["rosclaw.test.v2"])


def test_all_public_contracts_export_json_schema():
    schemas = export_contract_schemas(PUBLIC_CONTRACTS)
    assert "rosclaw.know.reference_pack.v2" in schemas
    assert "rosclaw.how.advice.v2" in schemas
    assert len(schemas) == len(PUBLIC_CONTRACTS)
