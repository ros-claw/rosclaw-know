"""scripts/how_health.py — standalone health gate for HOW formal experiments.

Does not depend on rosclaw_how so it can be imported from any harness repo.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

_OUTCOMES_FAILURE_WINDOW_SECONDS = 5 * 60


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


def _outcomes_failure_is_recent(health: dict[str, Any]) -> bool:
    """Return True if HOW has recorded an outcomes write failure recently."""
    failures = health.get("outcomes_write_failures") or {}
    if not failures.get("count"):
        return False
    last_ts = failures.get("last_ts")
    if not last_ts:
        return False
    try:
        last = datetime.fromisoformat(last_ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return (datetime.now(UTC) - last).total_seconds() <= _OUTCOMES_FAILURE_WINDOW_SECONDS
    except ValueError:
        return False


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

    if _outcomes_failure_is_recent(health):
        raise RuntimeError(
            f"Formal experiment aborted: recent outcomes write failure: {health.get('outcomes_write_failures')}"
        )

    return health


__all__ = ["assert_how_healthy", "fetch_healthz"]
