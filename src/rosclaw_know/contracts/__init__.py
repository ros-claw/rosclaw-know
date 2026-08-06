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
from .knowledge_v2 import KnowledgeUnitV2, KnowledgeVectorsV2, ProjectCardV2
from .reference_pack_v2 import (
    AdviceRecommendationV2,
    FeedbackGovernanceRecordV1,
    HowAdviceBundleV2,
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
)

__all__ = [
    "AdviceRecommendationV2",
    "ContractVersionError",
    "EvidenceRefV2",
    "FeedbackGovernanceRecordV1",
    "HowAdviceBundleV2",
    "IntegrityV2",
    "KnowledgeUnitV2",
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
    "SourceSnapshotV2",
    "StrictContract",
    "export_contract_schemas",
    "negotiate_schema_version",
]
