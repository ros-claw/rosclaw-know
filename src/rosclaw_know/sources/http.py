"""Small bounded HTTP transport used by source adapters."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .base import SourceLimitError, SourceUnavailableError


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class HttpTransport(Protocol):
    def get(
        self, url: str, *, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> HttpResponse: ...


class UrllibTransport:
    def get(
        self, url: str, *, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> HttpResponse:
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise SourceLimitError(f"response exceeds {max_bytes} bytes")
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise SourceLimitError(f"response exceeds {max_bytes} bytes")
                return HttpResponse(
                    status=int(response.status),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=body,
                )
        except SourceLimitError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise SourceUnavailableError(f"GET {url} failed: {type(exc).__name__}") from exc
