"""Run the opt-in real-source final audit without executing repository code."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from rosclaw_know.claims import audit_claims, compile_claims
from rosclaw_know.contracts import ResearchRequestV2, SourceRecordV2
from rosclaw_know.sources.arxiv import ArxivAdapter
from rosclaw_know.sources.base import SourceCandidate
from rosclaw_know.sources.github import GitHubAdapter
from rosclaw_know.store import InMemoryKnowStore
from rosclaw_know.wiki import compile_project_wiki
from rosclaw_know.wiki.knowledge_units import compile_knowledge_units

REPOSITORIES = (
    "ros-claw/rosclaw",
    "unitreerobotics/unitree_rl_lab",
    "realsenseai/realsense-ros",
    "FSoft-AI4Code/CodeWiki",
    "microsoft/graphrag",
    "upstash/context7",
    "stanford-oval/storm",
)


def _identifier(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _candidate(repository: str) -> SourceCandidate:
    url = f"https://github.com/{repository}"
    return SourceCandidate(
        source=SourceRecordV2(
            source_id=_identifier("source", url.casefold()),
            canonical_url=url,
            source_type="repository",
            title=repository.rsplit("/", 1)[-1],
            publisher=repository.split("/", 1)[0],
            repository=repository,
            trust_tier="primary",
            discovered_at=datetime.now(UTC),
        ),
        adapter="github",
        snapshot_ref="HEAD",
        authority_score=1.0,
        qualification_score=1.0,
        metadata={"full_name": repository, "default_branch": "HEAD"},
    )


async def _fetch_repository(
    repository: str, *, token: str, max_documents: int
) -> tuple[SourceCandidate, object, list]:
    adapter = GitHubAdapter(
        token=token,
        max_documents=max_documents,
        max_issue_documents=5,
        timeout=30.0,
    )
    candidate = _candidate(repository)
    snapshot = await adapter.snapshot(candidate)
    documents = [document async for document in adapter.fetch_documents(snapshot)]
    return candidate, snapshot, documents


def _select_critical_claims(claims: list, *, count: int = 10) -> list:
    predicates = (
        "indexed_component_count",
        "indexed_component_paths",
        "contains_component",
        "defines_symbols",
        "build_and_package_systems",
        "dependency_or_language_versions",
        "indexed_scope_limitation",
        "issue_release_or_pr",
        "compatibility_markers",
        "documents_overview",
    )
    selected = []
    for predicate in predicates:
        claim = next((item for item in claims if item.predicate == predicate), None)
        if claim is None:
            raise RuntimeError(f"critical claim category was not compiled: {predicate}")
        selected.append(claim)
    if len(selected) != count:
        raise RuntimeError(f"selected {len(selected)} claims; expected {count}")
    return selected


def _semantic_checks(compilation, claims: list) -> list[str]:
    failures: list[str] = []
    components = {component.path: component for component in compilation.components}
    page_summaries = {page.summary for page in compilation.pages}
    inventory = compilation.inventory
    expected = {
        "indexed_component_count": str(len(compilation.components)),
        "indexed_component_paths": ", ".join(
            component.path for component in compilation.components
        ),
        "build_and_package_systems": ", ".join(
            [*inventory.build_systems, *inventory.package_managers]
        )
        or "unknown",
        "dependency_or_language_versions": ", ".join(
            f"{key}={value}" for key, value in inventory.versions.items()
        )
        or ", ".join(inventory.languages)
        or "unknown",
        "compatibility_markers": ", ".join(
            [
                *inventory.robots,
                *inventory.simulators,
                *inventory.ros_distros,
                *inventory.frameworks,
            ]
        )
        or "unknown",
    }
    for claim in claims:
        component_path = claim.subject.split(":", 1)[-1]
        if claim.predicate == "contains_component" and claim.object not in components:
            failures.append(f"{claim.claim_id}:component_missing")
        elif claim.predicate == "defines_symbols":
            symbols = {item.strip() for item in claim.object.split(",")}
            component = components.get(component_path)
            if component is None or not symbols <= set(component.public_symbols):
                failures.append(f"{claim.claim_id}:symbol_missing")
        elif claim.claim_type == "derived_claim" and claim.object not in page_summaries:
            if claim.predicate.startswith("documents_"):
                failures.append(f"{claim.claim_id}:summary_not_from_compiled_page")
        if claim.predicate in expected and claim.object != expected[claim.predicate]:
            failures.append(f"{claim.claim_id}:inventory_value_mismatch")
    return failures


async def _paper_audit() -> dict:
    adapter = ArxivAdapter(timeout=30.0)
    request = ResearchRequestV2(
        request_id="final-robonaldo",
        topic="RoboNaldo arXiv 2606.11092",
        goal="pin the primary paper metadata and abstract",
        depth="shallow",
        max_sources=8,
        token_budget=20_000,
    )
    candidates = await adapter.discover(request)
    candidate = next(
        (item for item in candidates if "robonaldo" in item.source.title.casefold()), None
    )
    if candidate is None:
        raise RuntimeError("RoboNaldo was not returned by the arXiv primary-source adapter")
    snapshot = await adapter.snapshot(candidate)
    documents = [document async for document in adapter.fetch_documents(snapshot)]
    text = "\n".join(document.content for document in documents).casefold()
    terms = {
        "motion": "motion" in text,
        "curriculum": "curriculum" in text,
        "g1": "g1" in text,
        "football_or_kick": "football" in text or "kick" in text,
    }
    return {
        "title": candidate.source.title,
        "source_url": candidate.source.canonical_url,
        "snapshot_id": snapshot.snapshot_id,
        "version": snapshot.version_value,
        "document_hashes": [document.content_hash for document in documents],
        "term_checks": terms,
        "passed": bool(documents) and all(terms.values()),
    }


async def run(*, token: str, max_documents: int) -> dict:
    fetched = await asyncio.gather(
        *(
            _fetch_repository(
                repository,
                token=token,
                max_documents=max_documents,
            )
            for repository in REPOSITORIES
        )
    )
    project_reports = []
    selected_claims = []
    for candidate, snapshot, documents in fetched:
        store = InMemoryKnowStore()
        source = candidate.source.model_copy(update={"latest_snapshot_id": snapshot.snapshot_id})
        store.upsert_source(source)
        store.put_snapshot(snapshot)
        compilation = compile_project_wiki(
            source=source,
            snapshot=snapshot,
            documents=documents,
            store=store,
        )
        units = compile_knowledge_units(compilation, store=store)
        claims = compile_claims(compilation, units, source=source, store=store)
        critical = _select_critical_claims(claims)
        closure = audit_claims(store, critical)
        semantic_failures = _semantic_checks(compilation, critical)
        passed = closure.ok and not semantic_failures
        project_reports.append(
            {
                "repository": source.repository,
                "commit": snapshot.version_value,
                "snapshot_id": snapshot.snapshot_id,
                "documents": len(documents),
                "components": len(compilation.components),
                "wiki_pages": len(compilation.pages),
                "knowledge_units": len(units),
                "claims_compiled": len(claims),
                "critical_claims_checked": len(critical),
                "evidence_closure": closure.model_dump(mode="json"),
                "semantic_failures": semantic_failures,
                "passed": passed,
            }
        )
        selected_claims.extend(
            {
                "repository": source.repository,
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "object": claim.object,
                "snapshot_ids": claim.source_snapshot_ids,
                "evidence_ids": [evidence.evidence_id for evidence in claim.evidence_refs],
                "evidence_paths": [evidence.path for evidence in claim.evidence_refs],
                "evidence_content_hashes": [
                    evidence.content_hash for evidence in claim.evidence_refs
                ],
                "evidence_excerpts": [
                    evidence.excerpt[:500] for evidence in claim.evidence_refs
                ],
                "truth_quality": claim.truth_quality.score,
                "source_authority": claim.truth_quality.source_authority,
            }
            for claim in critical
        )
    paper = await _paper_audit()
    passed = (
        len(selected_claims) == 70
        and all(project["passed"] for project in project_reports)
        and paper["passed"]
    )
    return {
        "schema_version": "rosclaw.know.real_source_acceptance.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "safety": {
            "repository_code_executed": False,
            "fixed_commit_snapshots": True,
            "untrusted_text_treated_as_data": True,
        },
        "projects": project_reports,
        "critical_claim_count": len(selected_claims),
        "claims": selected_claims,
        "paper": paper,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-documents", type=int, default=20)
    args = parser.parse_args()
    report = asyncio.run(
        run(token=os.environ.get("GITHUB_TOKEN", ""), max_documents=args.max_documents)
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "passed": report["passed"],
                "critical_claim_count": report["critical_claim_count"],
                "projects": [
                    {
                        "repository": project["repository"],
                        "commit": project["commit"],
                        "documents": project["documents"],
                        "claims": project["critical_claims_checked"],
                        "passed": project["passed"],
                    }
                    for project in report["projects"]
                ],
                "paper": report["paper"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
