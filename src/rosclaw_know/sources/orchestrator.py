"""Bounded v2 research orchestration across registered read-only adapters."""

from __future__ import annotations

import asyncio

from pydantic import Field

from rosclaw_know.contracts import ResearchRequestV2
from rosclaw_know.contracts.base import StrictContract
from rosclaw_know.store import KnowStore
from rosclaw_know.wiki import compile_project_wiki
from rosclaw_know.wiki.knowledge_units import compile_knowledge_units

from .base import SourceAdapter, SourceCandidate
from .planner import ResearchPlan, build_research_plan


class ResearchRunResult(StrictContract):
    request_id: str
    plan: ResearchPlan
    status: str
    discovered: int
    qualified: int
    snapshots: int
    documents: int
    project_wikis: int
    knowledge_units: int
    source_ids: list[str] = Field(default_factory=list)
    snapshot_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResearchOrchestrator:
    def __init__(
        self,
        store: KnowStore,
        adapters: dict[str, SourceAdapter],
        *,
        per_adapter_timeout: float = 30.0,
        snapshot_timeout: float = 60.0,
    ) -> None:
        self.store = store
        self.adapters = dict(adapters)
        self.per_adapter_timeout = per_adapter_timeout
        self.snapshot_timeout = snapshot_timeout

    async def _discover_one(
        self, name: str, adapter: SourceAdapter, request: ResearchRequestV2
    ) -> tuple[list[SourceCandidate], str | None]:
        try:
            result = await asyncio.wait_for(
                adapter.discover(request), timeout=self.per_adapter_timeout
            )
            return result, None
        except Exception as exc:  # noqa: BLE001
            return [], f"adapter_discover_failed:{name}:{type(exc).__name__}"

    async def run(self, request: ResearchRequestV2) -> ResearchRunResult:
        plan = build_research_plan(request)
        tasks = [
            self._discover_one(name, adapter, request)
            for name, adapter in sorted(self.adapters.items())
        ]
        results = await asyncio.gather(*tasks)
        warnings = [warning for _, warning in results if warning]
        discovered = [candidate for candidates, _ in results for candidate in candidates]
        deduplicated: dict[str, SourceCandidate] = {}
        for candidate in discovered:
            url = candidate.source.canonical_url.casefold()
            existing = deduplicated.get(url)
            if existing is None or (
                candidate.qualification_score,
                candidate.authority_score,
            ) > (existing.qualification_score, existing.authority_score):
                deduplicated[url] = candidate
        qualified = sorted(
            deduplicated.values(),
            key=lambda item: (
                -item.qualification_score,
                -item.authority_score,
                item.source.source_id,
            ),
        )[: request.max_sources]

        source_ids: list[str] = []
        snapshot_ids: list[str] = []
        project_ids: list[str] = []
        document_count = 0
        unit_count = 0
        for candidate in qualified:
            adapter = self.adapters[candidate.adapter]
            try:
                snapshot = await asyncio.wait_for(
                    adapter.snapshot(candidate), timeout=self.snapshot_timeout
                )
                documents = [document async for document in adapter.fetch_documents(snapshot)]
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"adapter_ingest_failed:{candidate.adapter}:{candidate.source.source_id}:"
                    f"{type(exc).__name__}"
                )
                continue
            self.store.upsert_source(
                candidate.source.model_copy(update={"latest_snapshot_id": snapshot.snapshot_id})
            )
            self.store.put_snapshot(snapshot)
            source_ids.append(candidate.source.source_id)
            snapshot_ids.append(snapshot.snapshot_id)
            document_count += len(documents)
            if candidate.source.source_type == "repository" and documents:
                compilation = compile_project_wiki(
                    source=candidate.source,
                    snapshot=snapshot,
                    documents=documents,
                    store=self.store,
                )
                units = compile_knowledge_units(compilation, store=self.store)
                project_ids.append(compilation.project_card.project_id)
                unit_count += len(units)
            else:
                for document in documents:
                    self.store.put_document(document)
        return ResearchRunResult(
            request_id=request.request_id,
            plan=plan,
            status="completed" if snapshot_ids else "degraded",
            discovered=len(discovered),
            qualified=len(qualified),
            snapshots=len(snapshot_ids),
            documents=document_count,
            project_wikis=len(project_ids),
            knowledge_units=unit_count,
            source_ids=source_ids,
            snapshot_ids=snapshot_ids,
            project_ids=project_ids,
            warnings=warnings,
        )
