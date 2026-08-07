"""Bounded session-aware client for read-only Streamable HTTP MCP sources."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from .base import SourceLimitError, SourceUnavailableError


@dataclass(frozen=True)
class MCPHttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str


class MCPTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
        max_bytes: int,
    ) -> MCPHttpResponse: ...


class UrllibMCPTransport:
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
        max_bytes: int,
    ) -> MCPHttpResponse:
        try:
            request = urllib.request.Request(url, headers=headers, data=body, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise SourceLimitError(f"MCP response exceeds {max_bytes} bytes")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise SourceLimitError(f"MCP response exceeds {max_bytes} bytes")
                return MCPHttpResponse(
                    status=int(response.status),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=payload,
                    final_url=response.geturl(),
                )
        except SourceLimitError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise SourceUnavailableError(f"MCP POST failed: {type(exc).__name__}") from exc


def _decode_message(body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="strict").strip()
    if not text:
        return {}
    if text.startswith("{"):
        value = json.loads(text)
        if not isinstance(value, dict):
            raise SourceUnavailableError("MCP response was not a JSON object")
        return value
    messages = []
    for line in text.splitlines():
        if line.startswith("data:"):
            candidate = line.removeprefix("data:").strip()
            if candidate and candidate != "[DONE]":
                decoded = json.loads(candidate)
                if isinstance(decoded, dict):
                    messages.append(decoded)
    if not messages:
        raise SourceUnavailableError("MCP event stream contained no JSON message")
    return messages[-1]


def tool_text(result: dict[str, Any]) -> str:
    """Extract the public tool result without interpreting it as instructions."""

    structured = result.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"]
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    if parts:
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


class MCPStreamableHttpClient:
    """Minimal MCP client with exact-host, response-size and tool allowlists."""

    def __init__(
        self,
        endpoint: str,
        *,
        allowed_tools: set[str],
        headers: dict[str, str] | None = None,
        transport: MCPTransport | None = None,
        timeout: float = 30.0,
        max_response_bytes: int = 5_000_000,
        allow_http_for_tests: bool = False,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and not allow_http_for_tests:
            raise ValueError("external MCP endpoints must use HTTPS")
        if not parsed.hostname:
            raise ValueError("MCP endpoint requires a hostname")
        self.endpoint = endpoint
        self.allowed_host = parsed.hostname.casefold()
        self.allowed_tools = set(allowed_tools)
        self.extra_headers = dict(headers or {})
        self.transport = transport or UrllibMCPTransport()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.session_id: str | None = None
        self._next_id = 1
        self.server_info: dict[str, Any] = {}

    def _request(self, method: str, params: dict[str, Any], *, notification: bool = False):
        request_id = None if notification else self._next_id
        if not notification:
            self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
            "User-Agent": "rosclaw-know/knowledge-final-acceptance",
            **self.extra_headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = self.transport.post(
            self.endpoint,
            headers=headers,
            body=json.dumps(payload, separators=(",", ":")).encode(),
            timeout=self.timeout,
            max_bytes=self.max_response_bytes,
        )
        final_host = (urlparse(response.final_url).hostname or "").casefold()
        if final_host != self.allowed_host:
            raise SourceUnavailableError("MCP redirect escaped the configured host")
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        message = _decode_message(response.body)
        if notification:
            return message
        if message.get("error"):
            error = message["error"]
            raise SourceUnavailableError(
                f"MCP {method} failed: {error.get('code', 'unknown')} {error.get('message', '')}"
            )
        if message.get("id") != request_id:
            raise SourceUnavailableError("MCP response id did not match request")
        return message.get("result") or {}

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "rosclaw-know", "version": "1"},
            },
        )
        self.server_info = dict(result.get("serverInfo") or {})
        try:
            self._request("notifications/initialized", {}, notification=True)
        except SourceUnavailableError:
            # Sessionless public servers can reject notifications while still
            # supporting tools/list and tools/call correctly.
            pass
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.list_advertised_tools()
            if item.get("name") in self.allowed_tools
        ]

    def list_advertised_tools(self) -> list[dict[str, Any]]:
        if not self.server_info:
            self.initialize()
        result = self._request("tools/list", {})
        return [item for item in result.get("tools") or [] if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self.allowed_tools:
            raise ValueError(f"MCP tool is not allowlisted: {name}")
        if not self.server_info:
            self.initialize()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise SourceUnavailableError(f"MCP tool reported an error: {name}")
        return tool_text(result)


__all__ = [
    "MCPHttpResponse",
    "MCPStreamableHttpClient",
    "MCPTransport",
    "UrllibMCPTransport",
    "tool_text",
]
