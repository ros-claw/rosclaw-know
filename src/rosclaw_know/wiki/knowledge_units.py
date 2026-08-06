"""Compile evidence-backed retrieval units from Project Wiki pages."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from rosclaw_know.contracts import KnowledgeUnitV2, KnowledgeVectorsV2
from rosclaw_know.store import KnowStore

from .models import WikiCompilationResult

_TYPE_MAP = {
    "overview": "project_capability",
    "architecture": "design_pattern",
    "training": "implementation",
    "deployment": "integration_recipe",
    "robot_interface": "compatibility",
    "issues_and_releases": "failure_lesson",
    "configuration": "implementation",
}


def compile_knowledge_units(
    compilation: WikiCompilationResult, *, store: KnowStore | None = None
) -> list[KnowledgeUnitV2]:
    """Create conservative units whose statements are bounded by page evidence."""

    card = compilation.project_card
    now = datetime.now(UTC)
    units = []
    for page in compilation.pages:
        exact_paths = list(dict.fromkeys(evidence.path for evidence in page.evidence_refs))
        unit_id = "unit_" + hashlib.sha256(
            f"{page.snapshot_id}:{page.page_type}:{page.content_hash}".encode()
        ).hexdigest()[:24]
        units.append(
            KnowledgeUnitV2(
                knowledge_unit_id=unit_id,
                unit_type=_TYPE_MAP.get(page.page_type, "implementation"),
                title=f"{card.name}: {page.title}",
                problem=page.summary,
                mechanism=(
                    "The indexed source evidence documents the listed project paths; "
                    "unstated behavior remains unknown."
                ),
                implementation=(
                    "Inspect the pinned evidence before borrowing implementation from: "
                    + ", ".join(exact_paths)
                ),
                applicability=[card.name, page.page_type],
                limitations=list(
                    dict.fromkeys(
                        card.known_limitations
                        + ["Static compilation does not prove runtime behavior or hardware safety."]
                    )
                ),
                contraindications=[
                    "Do not treat this advisory unit as authorization for a physical action."
                ],
                software_constraints={
                    **({"ros": card.ros_distros[0]} if len(card.ros_distros) == 1 else {}),
                    **(
                        {"simulator": card.supported_simulators[0]}
                        if len(card.supported_simulators) == 1
                        else {}
                    ),
                },
                hardware_constraints=card.hardware_requirements,
                robot_constraints=card.supported_robots,
                source_snapshot_ids=[card.source_snapshot_id],
                evidence_refs=page.evidence_refs,
                confidence=0.65,
                status="verified",
                created_at=now,
                updated_at=now,
                vectors=KnowledgeVectorsV2(),
            )
        )
    if store is not None:
        with store.transaction():
            for unit in units:
                store.upsert_unit(unit)
    return units
