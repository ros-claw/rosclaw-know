"""Bounded v2 research orchestration across registered read-only adapters."""

from __future__ import annotations

import asyncio
import math

from pydantic import Field

from rosclaw_know.claims import audit_claims, compile_claims
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
    claims: int = 0
    bytes_ingested: int = 0
    estimated_tokens_ingested: int = 0
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
        run_timeout: float = 300.0,
        max_total_documents: int = 10_000,
        max_total_bytes: int = 50_000_000,
    ) -> None:
        self.store = store
        self.adapters = dict(adapters)
        self.per_adapter_timeout = per_adapter_timeout
        self.snapshot_timeout = snapshot_timeout
        self.run_timeout = run_timeout
        self.max_total_documents = max_total_documents
        self.max_total_bytes = max_total_bytes

    async def _fetch_documents(self, adapter: SourceAdapter, snapshot):
        async def collect():
            return [document async for document in adapter.fetch_documents(snapshot)]

        return await asyncio.wait_for(collect(), timeout=self.snapshot_timeout)

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
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.run_timeout
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
        bytes_ingested = 0
        estimated_tokens_ingested = 0
        unit_count = 0
        claim_count = 0
        for candidate in qualified:
            remaining = deadline - loop.time()
            if remaining <= 0:
                warnings.append("research_deadline_exceeded")
                break
            adapter = self.adapters[candidate.adapter]
            try:
                snapshot = await asyncio.wait_for(
                    adapter.snapshot(candidate),
                    timeout=min(self.snapshot_timeout, remaining),
                )
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                documents = await asyncio.wait_for(
                    self._fetch_documents(adapter, snapshot),
                    timeout=min(self.snapshot_timeout, remaining),
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"adapter_ingest_failed:{candidate.adapter}:{candidate.source.source_id}:"
                    f"{type(exc).__name__}"
                )
                continue
            bounded_documents = []
            candidate_bytes = 0
            candidate_tokens = 0
            for document in documents:
                document_bytes = document.size_bytes
                document_tokens = max(1, math.ceil(document_bytes / 4))
                if document_count + len(bounded_documents) >= self.max_total_documents:
                    warnings.append("document_limit_exhausted")
                    break
                if (
                    bytes_ingested + candidate_bytes + document_bytes
                    > self.max_total_bytes
                ):
                    warnings.append("byte_budget_exhausted")
                    break
                if (
                    estimated_tokens_ingested + candidate_tokens + document_tokens
                    > request.token_budget
                ):
                    warnings.append("token_budget_exhausted")
                    break
                bounded_documents.append(document)
                candidate_bytes += document_bytes
                candidate_tokens += document_tokens
            documents = bounded_documents
            if not documents:
                warnings.append(
                    f"adapter_ingest_empty_after_limits:{candidate.adapter}:"
                    f"{candidate.source.source_id}"
                )
                continue
            self.store.upsert_source(
                candidate.source.model_copy(update={"latest_snapshot_id": snapshot.snapshot_id})
            )
            self.store.put_snapshot(snapshot)
            source_ids.append(candidate.source.source_id)
            snapshot_ids.append(snapshot.snapshot_id)
            document_count += len(documents)
            bytes_ingested += candidate_bytes
            estimated_tokens_ingested += candidate_tokens
            if candidate.source.source_type == "repository" and documents:
                compilation = compile_project_wiki(
                    source=candidate.source,
                    snapshot=snapshot,
                    documents=documents,
                    store=self.store,
                )
                units = compile_knowledge_units(compilation, store=self.store)
                claims = compile_claims(
                    compilation,
                    units,
                    source=candidate.source,
                    store=self.store,
                )
                audit = audit_claims(self.store, claims)
                if not audit.ok:
                    warnings.append(
                        f"citation_verifier_failed:{candidate.source.source_id}:"
                        f"{len(audit.failures)}"
                    )
                project_ids.append(compilation.project_card.project_id)
                unit_count += len(units)
                claim_count += len(claims)
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
            claims=claim_count,
            bytes_ingested=bytes_ingested,
            estimated_tokens_ingested=estimated_tokens_ingested,
            source_ids=source_ids,
            snapshot_ids=snapshot_ids,
            project_ids=project_ids,
            warnings=warnings,
        )
