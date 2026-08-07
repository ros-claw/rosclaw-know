"""Public ROSClaw Know/How wire contracts.

Only this module is part of the v2 cross-repository Python contract surface.
Storage, compiler and retrieval implementation types are intentionally not
re-exported.
"""

from .base import (
    ContractVersionError,
    StrictContract,
    export_contract_schemas,
    negotiate_schema_version,
)
from .claim_v1 import (
    CompatibilityScopeV1,
    KnowledgeClaimV1,
    SourceDisagreementV1,
    TruthQualityV1,
)
from .knowledge_v2 import KnowledgeUnitV2, KnowledgeVectorsV2, ProjectCardV2
from .reference_pack_v2 import (
    AdviceCandidateDecisionV1,
    AdviceRecommendationV2,
    FeedbackGovernanceRecordV1,
    HowAdviceBundleV2,
    HowExplanationV1,
    KnowledgeUsageFeedbackV1,
    ReferenceComparisonV2,
    ReferenceContextV2,
    ReferencePackItemV2,
    ReferencePackV2,
)
from .research_v2 import ResearchConstraintsV2, ResearchRequestV2
from .source_v2 import EvidenceRefV2, IntegrityV2, SourceRecordV2, SourceSnapshotV2

PUBLIC_CONTRACTS = (
    ResearchRequestV2,
    SourceRecordV2,
    SourceSnapshotV2,
    EvidenceRefV2,
    ProjectCardV2,
    KnowledgeUnitV2,
    ReferencePackV2,
    HowAdviceBundleV2,
    KnowledgeUsageFeedbackV1,
    FeedbackGovernanceRecordV1,
    KnowledgeClaimV1,
    SourceDisagreementV1,
)

__all__ = [
    "AdviceCandidateDecisionV1",
    "AdviceRecommendationV2",
    "ContractVersionError",
    "CompatibilityScopeV1",
    "EvidenceRefV2",
    "FeedbackGovernanceRecordV1",
    "HowAdviceBundleV2",
    "HowExplanationV1",
    "IntegrityV2",
    "KnowledgeUnitV2",
    "KnowledgeClaimV1",
    "KnowledgeUsageFeedbackV1",
    "KnowledgeVectorsV2",
    "ProjectCardV2",
    "PUBLIC_CONTRACTS",
    "ReferenceComparisonV2",
    "ReferenceContextV2",
    "ReferencePackItemV2",
    "ReferencePackV2",
    "ResearchConstraintsV2",
    "ResearchRequestV2",
    "SourceRecordV2",
    "SourceDisagreementV1",
    "SourceSnapshotV2",
    "StrictContract",
    "TruthQualityV1",
    "export_contract_schemas",
    "negotiate_schema_version",
]
