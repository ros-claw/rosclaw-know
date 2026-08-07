from __future__ import annotations

import json

import pytest

from rosclaw_know.contracts import ResearchRequestV2
from rosclaw_know.sources import Context7Adapter, DeepWikiPublicAdapter, GitMCPAdapter
from rosclaw_know.sources.mcp_http import MCPHttpResponse, MCPStreamableHttpClient


class FakeMCPTransport:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    def post(self, url, *, headers, body, timeout, max_bytes):
        payload = json.loads(body)
        self.calls.append((payload, dict(headers)))
        method = payload["method"]
        if method == "notifications/initialized":
            return MCPHttpResponse(202, {}, b"", url)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {"tools": self.tools}
        else:
            name = payload["params"]["name"]
            arguments = payload["params"]["arguments"]
            if name == "resolve-library-id":
                text = "Library ID: /oceanbase/seekdb"
            elif name == "query-docs":
                text = f"Version-specific docs for {arguments['libraryId']}"
            else:
                text = f"{name}: pinned-looking derived result"
            result = {"structuredContent": {"result": text}}
        response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
        encoded = ("event: message\ndata: " + json.dumps(response) + "\n\n").encode()
        assert len(encoded) <= max_bytes
        return MCPHttpResponse(
            200,
            {"content-type": "text/event-stream", "mcp-session-id": "fixture-session"},
            encoded,
            url,
        )


def request(topic="ros-claw/rosclaw"):
    return ResearchRequestV2(
        request_id="external-mcp",
        topic=topic,
        goal="find architecture entrypoints and current version constraints",
        max_sources=8,
    )


def test_mcp_client_requires_https_and_exact_tool_allowlist():
    with pytest.raises(ValueError, match="HTTPS"):
        MCPStreamableHttpClient("http://example.invalid/mcp", allowed_tools=set())
    client = MCPStreamableHttpClient(
        "https://example.invalid/mcp",
        allowed_tools={"read"},
        transport=FakeMCPTransport([]),
    )
    with pytest.raises(ValueError, match="not allowlisted"):
        client.call_tool("write", {})


@pytest.mark.asyncio
async def test_deepwiki_public_adapter_is_derived_and_never_primary_evidence():
    tools = [
        {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        for name in ("read_wiki_structure", "read_wiki_contents", "ask_question")
    ]
    transport = FakeMCPTransport(tools)
    adapter = DeepWikiPublicAdapter(transport=transport)
    candidate = (await adapter.discover(request()))[0]
    snapshot = await adapter.snapshot(candidate)
    documents = [item async for item in adapter.fetch_documents(snapshot)]
    assert candidate.source.source_type == "derived_repository_documentation"
    assert candidate.source.trust_tier == "curated"
    assert len(documents) == 3
    assert all(item.metadata["code_executed"] is False for item in documents)
    assert all(
        item.metadata["evidence_policy"] == "derived_requires_pinned_primary_source"
        for item in documents
    )


@pytest.mark.asyncio
async def test_gitmcp_discovers_advertised_read_only_tools_only():
    tools = [
        {"name": "fetch_rosclaw_documentation", "inputSchema": {"properties": {}}},
        {
            "name": "search_rosclaw_documentation",
            "inputSchema": {"properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
        {
            "name": "search_rosclaw_code",
            "inputSchema": {"properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
        {"name": "dangerous_write", "inputSchema": {"properties": {}}},
    ]
    transport = FakeMCPTransport(tools)
    adapter = GitMCPAdapter(transport=transport)
    candidate = (await adapter.discover(request()))[0]
    snapshot = await adapter.snapshot(candidate)
    documents = [item async for item in adapter.fetch_documents(snapshot)]
    called = [payload["params"]["name"] for payload, _ in transport.calls if payload["method"] == "tools/call"]
    assert "dangerous_write" not in called
    assert {item.path for item in documents} == {
        ".rosclaw/gitmcp/documentation.md",
        ".rosclaw/gitmcp/documentation_search.md",
        ".rosclaw/gitmcp/code_search.md",
    }


@pytest.mark.asyncio
async def test_context7_records_resolved_version_documentation():
    tools = [
        {"name": "resolve-library-id", "inputSchema": {"properties": {}}},
        {"name": "query-docs", "inputSchema": {"properties": {}}},
    ]
    transport = FakeMCPTransport(tools)
    adapter = Context7Adapter(transport=transport)
    candidate = (await adapter.discover(request("seekdb 1.3 hybrid search")))[0]
    snapshot = await adapter.snapshot(candidate)
    documents = [item async for item in adapter.fetch_documents(snapshot)]
    assert len(documents) == 2
    assert all(item.metadata["library_id"] == "/oceanbase/seekdb" for item in documents)
