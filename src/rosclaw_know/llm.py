"""DeepSeek async client wrapper.

Wraps the OpenAI-compatible /v1/chat/completions endpoint so the rest of the
pipeline can call a single ``chat()`` helper. Honors a mock mode for tests
and includes simple retry + error-classification logic.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Any

import aiohttp

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_EXTRACTOR_MODEL,
    DEEPSEEK_MUSE_MODEL,
    MOCK_LLM,
)

log = logging.getLogger("rosclaw_know.llm")

# Token-spend tracking
_total_tokens = {"prompt": 0, "completion": 0}


def get_token_usage() -> dict[str, int]:
    return dict(_total_tokens)


def reset_token_usage() -> None:
    _total_tokens["prompt"] = 0
    _total_tokens["completion"] = 0


class LLMError(Exception):
    """Non-retryable LLM error."""


async def chat(
    session: aiohttp.ClientSession,
    system: str | None,
    user: str,
    *,
    model: str | None = None,
    response_format_json: bool = False,
    max_tokens: int = 500,
    temperature: float = 0.2,
    max_attempts: int = 3,
) -> str | None:
    """Call DeepSeek-style chat-completions; return raw assistant content.

    Returns None on non-recoverable error so the caller can simply skip the
    page instead of aborting the whole batch.
    """
    if MOCK_LLM:
        return _mock_response(system or "", user, response_format_json)

    if not DEEPSEEK_API_KEY:
        raise LLMError("DEEPSEEK_API_KEY not set and MOCK_LLM=0")

    url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    payload: dict[str, Any] = {
        "model": model or DEEPSEEK_EXTRACTOR_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                if resp.status == 429 or resp.status >= 500:
                    body = await resp.text()
                    log.warning("LLM transient %s on attempt %s: %s", resp.status, attempt, body[:200])
                    if attempt == max_attempts:
                        return None
                    await asyncio.sleep(delay + random.uniform(0, 0.5))
                    delay *= 2
                    continue
                if resp.status >= 400:
                    body = await resp.text()
                    log.error("LLM hard %s: %s", resp.status, body[:200])
                    return None
                data = await resp.json()
                # Track usage if the API reports it.
                usage = data.get("usage") or {}
                _total_tokens["prompt"] += int(usage.get("prompt_tokens", 0) or 0)
                _total_tokens["completion"] += int(usage.get("completion_tokens", 0) or 0)
                choices = data.get("choices") or []
                if not choices:
                    return None
                return choices[0].get("message", {}).get("content")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("LLM network error on attempt %s: %s", attempt, exc)
            if attempt == max_attempts:
                return None
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay *= 2
    return None


async def chat_json(
    session: aiohttp.ClientSession,
    system: str | None,
    user: str,
    **kwargs: Any,
) -> dict | None:
    """chat() that returns a parsed JSON dict, or None on parse failure."""
    kwargs.setdefault("response_format_json", True)
    raw = await chat(session, system, user, **kwargs)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        # Some models wrap JSON in code fences; try to recover.
        stripped = raw.strip().strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON: %s", raw[:200])
            return None


def _mock_response(system: str, user: str, want_json: bool) -> str:
    """Deterministic stub for plumbing tests."""
    if want_json:
        # Pick a domain based on simple keywords so weaver gets cross-domain edges.
        # Must match FRONTIER_DOMAINS in prompts.py so harvester validation passes.
        u = user.lower()
        if "pid" in u or "control" in u or "motor" in u or "torque" in u or "actuator" in u:
            domain = "Control_Locomotion"
        elif "cuda" in u or "kernel" in u or "gpu" in u or "latency" in u or "throughput" in u:
            domain = "Systems_Compute"
        elif "memory" in u or "kv-cache" in u or "context" in u or "reasoning" in u or "recall" in u:
            domain = "Memory_Reasoning"
        elif "signal" in u or "channel" in u or "fiber" in u or "depth" in u or "segmentation" in u or "image" in u or "vision" in u:
            domain = "Perception_Vision"
        elif "fluid" in u or "material" in u or "thermal" in u or "physical" in u or "simulation" in u or "dynamics" in u or "mesh" in u:
            domain = "World_Physics"
        elif "schedul" in u or "battery" in u or "optim" in u or "planning" in u or "navigation" in u:
            domain = "Planning_Decision"
        elif "rl" in u or "policy" in u or "training" in u or "learn" in u or "augment" in u or "sim-to-real" in u:
            domain = "Learning_Training"
        else:
            domain = "Control_Locomotion"
        # Use first 60 chars of user content to fake a symptom
        snippet = user.strip().split("\n", 1)[0][:80] or "generic_symptom"
        return json.dumps(
            {
                "symptom": f"MOCK: {snippet}",
                "domain": domain,
                "fix_pattern": "MOCK fix: clamp output, add hysteresis, reduce gain",
                "failed_attempt": "MOCK anti-pattern: blindly increased gain",
            }
        )
    return f"MOCK insight bridging the two domains via shared limiter mechanism."


__all__ = [
    "chat",
    "chat_json",
    "get_token_usage",
    "reset_token_usage",
    "LLMError",
    "DEEPSEEK_EXTRACTOR_MODEL",
    "DEEPSEEK_MUSE_MODEL",
]
