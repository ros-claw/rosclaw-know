"""Evidence guard, compatibility scoring and progressive Reference Packs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Protocol

from rosclaw_know.contracts import (
    KnowledgeUnitV2,
    ReferenceComparisonV2,
    ReferenceContextV2,
    ReferencePackItemV2,
    ReferencePackV2,
)
from rosclaw_know.store import KnowStore, SearchHit

from .planner import build_retrieval_plan


class EmbeddingProvider(Protocol):
    def embed(self, texts: dict[str, str]) -> dict[str, list[float]]: ...


def _hash_payload(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _estimate_tokens(value: object) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)


def _compatibility(unit: KnowledgeUnitV2, context: ReferenceContextV2) -> tuple[float, list[str]]:
    warnings = []
    expected = dict(context.software_versions)
    if context.ros_distro:
        expected.setdefault("ros", context.ros_distro)
    if context.simulator:
        expected.setdefault("simulator", context.simulator)
    for name, requested in expected.items():
        available = unit.software_constraints.get(name)
        if available and available.casefold() != requested.casefold():
            warnings.append(f"{name} mismatch: context={requested}, reference={available}")
        elif not available:
            warnings.append(f"{name} compatibility unknown for requested={requested}")
    if context.robot:
        if unit.robot_constraints and context.robot not in unit.robot_constraints:
            warnings.append(
                f"robot mismatch: context={context.robot}, reference={','.join(unit.robot_constraints)}"
            )
        elif not unit.robot_constraints:
            warnings.append(f"robot compatibility unknown for requested={context.robot}")
    return max(0.0, 1.0 - 0.25 * len(warnings)), warnings


def _project_for(store: KnowStore, unit_id: str) -> str | None:
    for relation in store.related(unit_id, limit=20):
        if relation.from_id == unit_id and relation.to_type == "project":
            return relation.to_id
        if relation.to_id == unit_id and relation.from_type == "project":
            return relation.from_id
    return None


def _source_version(store: KnowStore, unit: KnowledgeUnitV2) -> str:
    versions = []
    for snapshot_id in unit.source_snapshot_ids:
        snapshot = store.get_snapshot(snapshot_id)
        if snapshot is not None:
            versions.append(f"{snapshot.version_kind}:{snapshot.version_value}")
    return ", ".join(versions) or "unknown"


def _expand_relations(store: KnowStore, hits: list[SearchHit], limit: int) -> list[SearchHit]:
    expanded = list(hits)
    seen = {hit.knowledge_unit_id for hit in hits}
    for hit in hits:
        for relation in store.related(hit.knowledge_unit_id, limit=20):
            neighbor = (
                relation.to_id if relation.from_id == hit.knowledge_unit_id else relation.from_id
            )
            if neighbor in seen or store.get_unit(neighbor) is None:
                continue
            seen.add(neighbor)
            expanded.append(
                SearchHit(
                    knowledge_unit_id=neighbor,
                    score=hit.score * relation.confidence * 0.75,
                    score_breakdown={
                        "relation_parent": hit.score,
                        "relation_confidence": relation.confidence,
                    },
                    matched_by=[f"relation:{relation.relation_type}"],
                    warnings=[],
                )
            )
            if len(expanded) >= limit:
                return expanded
    return expanded


class ReferencePackBuilder:
    def __init__(self, store: KnowStore, *, embedding_provider: EmbeddingProvider | None = None):
        self.store = store
        self.embedding_provider = embedding_provider

    def retrieve(
        self,
        *,
        query: str,
        context: ReferenceContextV2,
        top_k: int = 10,
        token_budget: int = 8_000,
    ) -> ReferencePackV2:
        plan = build_retrieval_plan(query, context, requested_limit=top_k)
        vectors = (
            self.embedding_provider.embed(plan.semantic_queries)
            if self.embedding_provider is not None
            else None
        )
        hits = self.store.search(
            query,
            query_vectors=vectors,
            filters=plan.filters,
            limit=plan.recall_limit,
        )
        hits = _expand_relations(self.store, hits, plan.recall_limit)
        hits.sort(key=lambda item: (-item.score, item.knowledge_unit_id))
        warnings: list[str] = []
        units: list[tuple[SearchHit, KnowledgeUnitV2, list[str]]] = []
        for hit in hits:
            unit = self.store.get_unit(hit.knowledge_unit_id)
            if unit is None:
                continue
            # Evidence guard: generated or legacy units without a resolvable
            # immutable snapshot never cross into the final pack.
            if not unit.evidence_refs or any(
                self.store.get_snapshot(evidence.snapshot_id) is None
                for evidence in unit.evidence_refs
            ):
                warnings.append(f"evidence_guard_dropped:{unit.knowledge_unit_id}")
                continue
            compatibility, incompatibilities = _compatibility(unit, context)
            adjusted = hit.model_copy(
                update={
                    "score": hit.score * compatibility,
                    "score_breakdown": {
                        **hit.score_breakdown,
                        "compatibility": compatibility,
                        "confidence": unit.confidence,
                    },
                }
            )
            units.append((adjusted, unit, incompatibilities))
        units.sort(key=lambda item: (-item[0].score, item[1].knowledge_unit_id))

        index = self.store.latest_index_version()
        index_version = index.index_version if index else "unversioned"
        if index is None:
            warnings.append("index_version_missing")
        if not self.store.capabilities.ai_rerank:
            warnings.append("reranker_used=false; deterministic_fallback=rrf")

        context_hash = _hash_payload(context.model_dump(mode="json"))
        pack_id = f"reference_pack_{_hash_payload({'query': query, 'context': context_hash, 'index': index_version})[:24]}"
        items = []
        consumed = 0
        continuation_cursor = None
        for rank, (hit, unit, incompatibilities) in enumerate(units[:top_k], start=1):
            item = ReferencePackItemV2(
                rank=rank,
                project_id=_project_for(self.store, unit.knowledge_unit_id),
                knowledge_unit_ids=[unit.knowledge_unit_id],
                title=unit.title,
                why_relevant=(
                    f"Matched by {', '.join(hit.matched_by) or 'structured retrieval'}; "
                    f"confidence={unit.confidence:.2f}."
                ),
                relevance_dimensions=hit.matched_by,
                mechanism=unit.mechanism,
                what_to_borrow=[unit.implementation],
                exact_files=list(dict.fromkeys(evidence.path for evidence in unit.evidence_refs)),
                exact_sections=list(
                    dict.fromkeys(
                        evidence.section for evidence in unit.evidence_refs if evidence.section
                    )
                ),
                incompatibilities=incompatibilities,
                limitations=unit.limitations,
                adaptation_needed=(
                    ["Resolve compatibility warnings before applying this reference."]
                    if incompatibilities
                    else []
                ),
                source_version=_source_version(self.store, unit),
                evidence_refs=unit.evidence_refs,
                score=hit.score,
                score_breakdown=hit.score_breakdown,
            )
            item_cost = _estimate_tokens(item.model_dump(mode="json"))
            if consumed + item_cost > token_budget:
                continuation_cursor = unit.knowledge_unit_id
                break
            items.append(item)
            consumed += item_cost
        truncated = continuation_cursor is not None or len(units) > len(items)
        if truncated and continuation_cursor is None and len(items) < len(units):
            continuation_cursor = units[len(items)][1].knowledge_unit_id

        applicability_counts = Counter(
            value for _, unit, _ in units[: len(items)] for value in unit.applicability
        )
        comparison = ReferenceComparisonV2(
            shared_principles=sorted(
                value for value, count in applicability_counts.items() if count > 1
            ),
            conflicting_assumptions=sorted(
                {
                    warning
                    for _, _, item_warnings in units[: len(items)]
                    for warning in item_warnings
                }
            ),
            route_tradeoffs=list(
                dict.fromkeys(
                    limitation
                    for _, unit, _ in units[: len(items)]
                    for limitation in unit.limitations
                )
            )[:20],
            preferred_references=[item.knowledge_unit_ids[0] for item in items[:3]],
        )
        pack = ReferencePackV2(
            reference_pack_id=pack_id,
            query=query,
            context=context,
            generated_at=datetime.now(UTC),
            index_version=index_version,
            items=items,
            comparison=comparison,
            recommended_reading_order=[item.knowledge_unit_ids[0] for item in items],
            suggested_next_checks=[
                "Open the cited file/section at the pinned source version.",
                "Verify compatibility against current runtime versions before implementation.",
            ],
            open_questions=(
                ["No evidence-backed knowledge unit survived retrieval."] if not items else []
            ),
            token_budget=token_budget,
            truncated=truncated,
            continuation_cursor=continuation_cursor,
            warnings=list(
                dict.fromkeys(warnings + [warning for hit in hits for warning in hit.warnings])
            ),
        )
        self.store.put_reference_pack(pack)
        return pack
