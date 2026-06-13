"""scripts/how_health.py — standalone health gate for HOW formal experiments.

Does not depend on rosclaw_how so it can be imported from any harness repo.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def fetch_healthz(base_url: str, api_key: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch /healthz and return the parsed JSON payload."""
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/healthz",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def assert_how_healthy(base_url: str, api_key: str | None = None) -> dict[str, Any]:
    """Abort a formal experiment if HOW is not in a publishable state."""
    health = fetch_healthz(base_url, api_key=api_key)

    if health.get("status") != "ok":
        raise RuntimeError(f"Formal experiment aborted: HOW is not healthy: {health}")

    if health.get("router_backend") != "seekdb":
        raise RuntimeError(
            f"Formal experiment aborted: router_backend must be seekdb: {health}"
        )

    topic_filter = health.get("topic_filter") or {}
    if not topic_filter.get("enabled"):
        raise RuntimeError(
            f"Formal experiment aborted: topic_filter disabled: {health}"
        )

    if not health.get("assets_loaded"):
        raise RuntimeError(
            f"Formal experiment aborted: assets not loaded: {health}"
        )

    if health.get("missing_assets"):
        raise RuntimeError(
            f"Formal experiment aborted: missing assets: {health}"
        )

    return health


__all__ = ["assert_how_healthy", "fetch_healthz"]
