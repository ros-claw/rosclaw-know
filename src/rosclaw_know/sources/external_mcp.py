"""Read-only derived-source adapters for DeepWiki, GitMCP and Context7."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from rosclaw_know.contracts import (
    IntegrityV2,
    ResearchRequestV2,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.store import DocumentRecord

from .base import SourceCandidate, SourceUnavailableError
from .mcp_http import MCPStreamableHttpClient, MCPTransport
from .security import normalize_untrusted_text

_REPOSITORY = re.compile(
    r"(?:https?://github\.com/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
)


def _id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _repository(request: ResearchRequestV2) -> str | None:
    for value in (request.topic, request.goal):
        match = _REPOSITORY.search(value)
        if match:
            return f"{match.group('owner')}/{match.group('repo').removesuffix('.git')}"
    return None


def _snapshot(
    *, source_id: str, namespace: str, content: dict[str, str]
) -> SourceSnapshotV2:
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return SourceSnapshotV2(
        snapshot_id=_id("snapshot", f"{namespace}:{source_id}:{digest}"),
        source_id=source_id,
        version_kind="document_version",
        version_value=f"content-sha256:{digest}",
        fetched_at=datetime.now(UTC),
        content_hash=digest,
        integrity=IntegrityV2(sha256=digest),
    )


def _document(
    snapshot: SourceSnapshotV2,
    *,
    path: str,
    content: str,
    url: str,
    adapter: str,
    repository: str | None = None,
    library_id: str | None = None,
) -> DocumentRecord:
    normalized, signals = normalize_untrusted_text(content)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return DocumentRecord(
        document_id=_id("document", f"{snapshot.snapshot_id}:{path}:{digest}"),
        snapshot_id=snapshot.snapshot_id,
        document_type="derived_documentation",
        path=path,
        title=path.rsplit("/", 1)[-1],
        language="markdown",
        content=normalized,
        content_hash=digest,
        size_bytes=len(normalized.encode()),
        metadata={
            "url": url,
            "adapter": adapter,
            "repository": repository,
            "library_id": library_id,
            "untrusted_source": True,
            "prompt_injection_signals": signals,
            "code_executed": False,
            "authority_tier": "B",
            "evidence_policy": "derived_requires_pinned_primary_source",
        },
        created_at=snapshot.fetched_at,
    )


class DeepWikiPublicAdapter:
    name = "deepwiki"

    def __init__(
        self,
        *,
        endpoint: str = "https://mcp.deepwiki.com/mcp",
        transport: MCPTransport | None = None,
        timeout: float = 45.0,
        max_response_bytes: int = 5_000_000,
    ) -> None:
        self.endpoint = endpoint
        self.transport = transport
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._state: dict[str, dict[str, str]] = {}

    def _client(self) -> MCPStreamableHttpClient:
        return MCPStreamableHttpClient(
            self.endpoint,
            allowed_tools={"read_wiki_structure", "read_wiki_contents", "ask_question"},
            transport=self.transport,
            timeout=self.timeout,
            max_response_bytes=self.max_response_bytes,
            allow_http_for_tests=self.endpoint.startswith("http://"),
        )

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        repository = _repository(request)
        if repository is None:
            return []
        url = f"https://deepwiki.com/{repository}"
        return [
            SourceCandidate(
                source=SourceRecordV2(
                    source_id=_id("source", f"deepwiki:{repository}"),
                    canonical_url=url,
                    source_type="derived_repository_documentation",
                    title=f"DeepWiki: {repository}",
                    publisher="DeepWiki",
                    repository=repository,
                    trust_tier="curated",
                    discovered_at=datetime.now(UTC),
                    provenance_status="verified",
                    tags=["derived", "repository_wiki"],
                ),
                adapter=self.name,
                authority_score=0.65,
                qualification_score=0.8,
                metadata={
                    "repository": repository,
                    "question": request.goal,
                    "canonical_primary": f"https://github.com/{repository}",
                },
            )
        ]

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        repository = str(candidate.metadata["repository"])
        question = str(candidate.metadata.get("question") or "Explain the repository architecture.")

        def fetch() -> tuple[dict[str, str], dict[str, Any]]:
            client = self._client()
            client.initialize()
            content = {
                "structure": client.call_tool(
                    "read_wiki_structure", {"repoName": repository}
                ),
                "contents": client.call_tool("read_wiki_contents", {"repoName": repository}),
                "answer": client.call_tool(
                    "ask_question", {"repoName": repository, "question": question}
                ),
            }
            return content, client.server_info

        content, server_info = await asyncio.to_thread(fetch)
        snapshot = _snapshot(
            source_id=candidate.source.source_id,
            namespace="deepwiki",
            content=content,
        )
        self._state[snapshot.snapshot_id] = {
            **content,
            "repository": repository,
            "server_version": str(server_info.get("version") or "unknown"),
        }
        return snapshot

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        state = self._state.get(snapshot.snapshot_id)
        if state is None:
            raise SourceUnavailableError("DeepWiki snapshot state is unavailable")
        repository = state["repository"]
        for name in ("structure", "contents", "answer"):
            yield _document(
                snapshot,
                path=f".rosclaw/deepwiki/{name}.md",
                content=state[name],
                url=f"https://deepwiki.com/{repository}",
                adapter=self.name,
                repository=repository,
            )


class GitMCPAdapter:
    name = "gitmcp"

    def __init__(
        self,
        *,
        endpoint_template: str = "https://gitmcp.io/{repository}",
        transport: MCPTransport | None = None,
        timeout: float = 45.0,
        max_response_bytes: int = 5_000_000,
    ) -> None:
        self.endpoint_template = endpoint_template
        self.transport = transport
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._state: dict[str, dict[str, str]] = {}

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        repository = _repository(request)
        if repository is None:
            return []
        return [
            SourceCandidate(
                source=SourceRecordV2(
                    source_id=_id("source", f"gitmcp:{repository}"),
                    canonical_url=f"https://gitmcp.io/{repository}",
                    source_type="derived_repository_retrieval",
                    title=f"GitMCP: {repository}",
                    publisher="GitMCP",
                    repository=repository,
                    trust_tier="curated",
                    discovered_at=datetime.now(UTC),
                    provenance_status="verified",
                    tags=["derived", "documentation_search", "code_search"],
                ),
                adapter=self.name,
                authority_score=0.65,
                qualification_score=0.75,
                metadata={"repository": repository, "query": request.goal},
            )
        ]

    @staticmethod
    def _tool_arguments(tool: dict[str, Any], *, repository: str, query: str) -> dict[str, Any]:
        properties = (tool.get("inputSchema") or {}).get("properties") or {}
        arguments: dict[str, Any] = {}
        for name in properties:
            folded = name.casefold()
            if folded in {"query", "search_query", "question"}:
                arguments[name] = query
            elif folded in {"repository", "repo", "reponame"}:
                arguments[name] = repository
            elif folded == "owner":
                arguments[name] = repository.split("/", 1)[0]
            elif folded in {"repo_name", "name"}:
                arguments[name] = repository.split("/", 1)[1]
        missing = set((tool.get("inputSchema") or {}).get("required") or []) - set(arguments)
        if missing:
            raise SourceUnavailableError(
                f"GitMCP advertised unsupported required arguments: {sorted(missing)}"
            )
        return arguments

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        repository = str(candidate.metadata["repository"])
        query = str(candidate.metadata.get("query") or "architecture entrypoints configuration")
        endpoint = self.endpoint_template.format(repository=quote(repository, safe="/"))

        def fetch() -> tuple[dict[str, str], dict[str, Any]]:
            client = MCPStreamableHttpClient(
                endpoint,
                allowed_tools=set(),
                transport=self.transport,
                timeout=self.timeout,
                max_response_bytes=self.max_response_bytes,
                allow_http_for_tests=endpoint.startswith("http://"),
            )
            client.initialize()
            advertised = client.list_advertised_tools()
            allowed = []
            for tool in advertised:
                name = str(tool.get("name") or "")
                if name == "fetch_url_content" or name.endswith(
                    ("_documentation", "_code")
                ):
                    allowed.append(tool)
                    client.allowed_tools.add(name)
            selected: list[tuple[str, str, dict[str, Any]]] = []
            for tool in allowed:
                name = str(tool["name"])
                if name == "fetch_url_content":
                    continue
                key = (
                    "documentation"
                    if name.startswith("fetch_")
                    else "documentation_search"
                    if name.endswith("_documentation")
                    else "code_search"
                )
                if any(existing[0] == key for existing in selected):
                    continue
                arguments = self._tool_arguments(tool, repository=repository, query=query)
                selected.append((key, name, arguments))
            content: dict[str, str] = {}

            def call(selected_tool: tuple[str, str, dict[str, Any]]):
                key, name, arguments = selected_tool
                tool_client = MCPStreamableHttpClient(
                    endpoint,
                    allowed_tools={name},
                    transport=self.transport,
                    timeout=self.timeout,
                    max_response_bytes=self.max_response_bytes,
                    allow_http_for_tests=endpoint.startswith("http://"),
                )
                tool_client.initialize()
                return key, tool_client.call_tool(name, arguments)

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(selected) or 1)) as pool:
                for key, value in pool.map(call, selected):
                    content[key] = value
            if not content:
                raise SourceUnavailableError("GitMCP exposed no read-only documentation tools")
            return content, client.server_info

        content, server_info = await asyncio.to_thread(fetch)
        snapshot = _snapshot(
            source_id=candidate.source.source_id,
            namespace="gitmcp",
            content=content,
        )
        self._state[snapshot.snapshot_id] = {
            **content,
            "repository": repository,
            "server_version": str(server_info.get("version") or "unknown"),
        }
        return snapshot

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        state = self._state.get(snapshot.snapshot_id)
        if state is None:
            raise SourceUnavailableError("GitMCP snapshot state is unavailable")
        repository = state["repository"]
        for name in ("documentation", "documentation_search", "code_search"):
            if name not in state:
                continue
            yield _document(
                snapshot,
                path=f".rosclaw/gitmcp/{name}.md",
                content=state[name],
                url=f"https://gitmcp.io/{repository}",
                adapter=self.name,
                repository=repository,
            )


class Context7Adapter:
    name = "context7"

    def __init__(
        self,
        *,
        endpoint: str = "https://mcp.context7.com/mcp",
        api_key: str | None = None,
        transport: MCPTransport | None = None,
        timeout: float = 45.0,
        max_response_bytes: int = 5_000_000,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key if api_key is not None else os.environ.get("CONTEXT7_API_KEY", "")
        self.transport = transport
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._state: dict[str, dict[str, str]] = {}

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        if request.source_types and not set(request.source_types) & {
            "official_documentation",
            "web",
        }:
            return []
        library = next(iter(request.constraints.software_versions), request.topic).strip()
        if not library:
            return []
        return [
            SourceCandidate(
                source=SourceRecordV2(
                    source_id=_id("source", f"context7:{library.casefold()}:{request.goal}"),
                    canonical_url="https://context7.com/",
                    source_type="derived_version_documentation",
                    title=f"Context7: {library}",
                    publisher="Context7",
                    trust_tier="curated",
                    discovered_at=datetime.now(UTC),
                    provenance_status="verified",
                    tags=["derived", "version_specific_documentation"],
                ),
                adapter=self.name,
                authority_score=0.65,
                qualification_score=0.7,
                metadata={"library": library, "query": request.goal},
            )
        ]

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        library = str(candidate.metadata["library"])
        query = str(candidate.metadata["query"])

        def fetch() -> tuple[dict[str, str], dict[str, Any]]:
            headers = {"X-Context7-API-Key": self.api_key} if self.api_key else {}
            client = MCPStreamableHttpClient(
                self.endpoint,
                allowed_tools={"resolve-library-id", "query-docs"},
                headers=headers,
                transport=self.transport,
                timeout=self.timeout,
                max_response_bytes=self.max_response_bytes,
                allow_http_for_tests=self.endpoint.startswith("http://"),
            )
            client.initialize()
            resolution = client.call_tool(
                "resolve-library-id", {"libraryName": library, "query": query}
            )
            library_ids = re.findall(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", resolution)
            if not library_ids:
                raise SourceUnavailableError("Context7 did not resolve a library id")
            library_id = library_ids[0]
            documentation = client.call_tool(
                "query-docs", {"libraryId": library_id, "query": query}
            )
            return {
                "resolution": resolution,
                "documentation": documentation,
                "library_id": library_id,
            }, client.server_info

        content, server_info = await asyncio.to_thread(fetch)
        snapshot = _snapshot(
            source_id=candidate.source.source_id,
            namespace="context7",
            content=content,
        )
        self._state[snapshot.snapshot_id] = {
            **content,
            "server_version": str(server_info.get("version") or "unknown"),
        }
        return snapshot

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        state = self._state.get(snapshot.snapshot_id)
        if state is None:
            raise SourceUnavailableError("Context7 snapshot state is unavailable")
        library_id = state["library_id"]
        for name in ("resolution", "documentation"):
            yield _document(
                snapshot,
                path=f".rosclaw/context7/{name}.md",
                content=state[name],
                url=f"https://context7.com/{library_id.lstrip('/')}",
                adapter=self.name,
                library_id=library_id,
            )


__all__ = ["Context7Adapter", "DeepWikiPublicAdapter", "GitMCPAdapter"]
